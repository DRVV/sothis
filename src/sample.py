import getpass
import os
from dotenv import load_dotenv

load_dotenv('../.env')


def _set_env(var: str):
    if not os.environ.get(var):
        os.environ[var] = getpass.getpass(f"{var}: ")

model_name = os.getenv("OPENAI_MODEL_NAME")
_set_env("OPENAI_API_KEY")


from typing import Literal
# from langchain.chat_models import ChatOpenAI
from langgraph.graph import MessagesState, StateGraph, START
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command, interrupt
from langgraph.checkpoint.memory import MemorySaver

from langchain_openai import ChatOpenAI
# Initialize the language model using OpenAI's API.
model = ChatOpenAI(model_name=model_name, temperature=0.7)

import random
from typing import Annotated, Literal
from langchain_core.tools import tool
from langchain_core.tools.base import InjectedToolCallId
from langgraph.prebuilt import InjectedState
def make_handoff_tool(*, agent_name: str):
    """Create a tool that can return handoff via a Command"""
    tool_name = f"transfer_to_{agent_name}"

    @tool(tool_name)
    def handoff_to_agent(
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ):
        """Ask another agent for help."""
        tool_message = {
            "role": "tool",
            "content": f"Successfully transferred to {agent_name}",
            "name": tool_name,
            "tool_call_id": tool_call_id,
        }
        return Command(
            # navigate to another agent node in the PARENT graph
            goto=agent_name,
            graph=Command.PARENT,
            # This is the state update that the agent `agent_name` will see when it is invoked.
            # We're passing agent's FULL internal message history AND adding a tool message to make sure
            # the resulting chat history is valid.
            update={"messages": state["messages"] + [tool_message]},
        )

    return handoff_to_agent

#########################################
# Define the Philosopher Agents
#########################################

# Socrates: His handoff tool sends control to Plato.
socrates_tools = [
    make_handoff_tool(agent_name="plato"),
]
socrates_agent = create_react_agent(
    model,
    socrates_tools,
    prompt=(
        "You are Socrates, the renowned philosopher known for your method of questioning. "
        "Engage in a deep, thoughtful discussion on the given topic. "
        "Ensure you include a clear, human-readable response before handing off control to Plato."
    ),
)

def call_socrates(state: MessagesState) -> Command[Literal["plato", "human"]]:
    # Invoke Socrates' agent with the current message history.
    response = socrates_agent.invoke(state)
    # After Socrates answers, hand off to the human node.
    return Command(update=response, goto="human")


# Plato: His handoff tool sends control back to Socrates.
plato_tools = [
    make_handoff_tool(agent_name="socrates"),
]
plato_agent = create_react_agent(
    model,
    plato_tools,
    prompt=(
        "You are Plato, the influential philosopher and disciple of Socrates. "
        "Provide a reflective and reasoned analysis of the discussion topic. "
        "Ensure your response is human-readable before handing off control back to Socrates."
    ),
)

def call_plato(state: MessagesState) -> Command[Literal["socrates", "human"]]:
    response = plato_agent.invoke(state)
    return Command(update=response, goto="human")

#########################################
# Human Node: Collecting User Input
#########################################

def human_node(
    state: MessagesState, config
) -> Command[Literal["socrates", "plato", "human"]]:
    """
    This node collects user input and then routes the conversation back to
    the last active philosopher. It inspects the metadata (triggers) to determine
    which agent should receive the new input.
    """
    user_input = interrupt(value="Ready for user input.")
    langgraph_triggers = config["metadata"]["langgraph_triggers"]
    if len(langgraph_triggers) != 1:
        raise AssertionError("Expected exactly 1 trigger in human node")
    active_agent = langgraph_triggers[0].split(":")[1]
    return Command(
        update={
            "messages": [
                {
                    "role": "human",
                    "content": user_input,
                }
            ]
        },
        goto=active_agent,
    )

#########################################
# Build the State Graph
#########################################

builder = StateGraph(MessagesState)
builder.add_node("socrates", call_socrates)
builder.add_node("plato", call_plato)
builder.add_node("human", human_node)

# Start the discussion with Socrates.
builder.add_edge(START, "socrates")

# Set up a memory checkpointer.
checkpointer = MemorySaver()

# Compile the graph.
graph = builder.compile(checkpointer=checkpointer)

#########################################
# Running the Multiagent Discussion
#########################################

if __name__ == "__main__":
    print("Multiagent Philosophical Discussion System is ready.")
    # At this point the system is set up.
    # Depending on your integration, you can now call graph.run() within an interactive loop
    # or embed this into a web service to continuously collect human input and exchange messages.
    import uuid

    thread_config = {"configurable": {"thread_id": uuid.uuid4()}}

    inputs = [
        # 1st round of conversation,
        {
            "messages": [
                {"role": "user", "content": "Can you two, Socrates and Plato, discuss what the physical world is?"}
            ]
        },
        # Since we're using `interrupt`, we'll need to resume using the Command primitive.
        # 2nd round of conversation,
        # Command(
        #     resume="what is the difference between idea and the physical being?"
        # ),
        # # 3rd round of conversation,
        # Command(
        #     resume="i like the first one. could you recommend something to do near the hotel?"
        # ),
    ]

    for idx, user_input in enumerate(inputs):
        print()
        print(f"--- Conversation Turn {idx + 1} ---")
        print()
        print(f"User: {user_input}")
        print()
        for update in graph.stream(
            user_input,
            config=thread_config,
            stream_mode="updates",
        ):
            for node_id, value in update.items():
                if isinstance(value, dict) and value.get("messages", []):
                    last_message = value["messages"][-1]
                    if isinstance(last_message, dict) or last_message.type != "ai":
                        continue
                    print(f"{node_id}: {last_message.content}")
