"""Build the LangChain prompt-to-Ollama generation pipeline.

RAG_PROMPT_TEMPLATE defines reviews and question placeholders. prompt, model,
and chain are shared instances created on import with config defaults.
Callers retrieve reviews separately and pass them to the chain.
"""

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_ollama.llms import OllamaLLM

import config

RAG_PROMPT_TEMPLATE = """
Your are an expert in answering questions about a pizza restaurant

Here are some relevant reviews: {reviews}

Here is the question: {question}  
"""


def build_rag_chain(
    model_name: str = config.LLM_MODEL,
    base_url: str = config.OLLAMA_BASE_URL,
    template: str = RAG_PROMPT_TEMPLATE,
) -> tuple[ChatPromptTemplate, OllamaLLM, Runnable]:
    """Build a prompt, Ollama model client, and composed generation chain.

    Args:
        model_name: Ollama generation model identifier.
        base_url: Base URL of the Ollama server.
        template: LangChain prompt text; the default uses reviews and question.

    Returns:
        A tuple containing the ChatPromptTemplate, OllamaLLM, and prompt-to-model
        Runnable. Call the Runnable with the variables required by template.

    Notes:
        Retrieval is performed by callers before invoking the generation chain.
    """
    prompt = ChatPromptTemplate.from_template(template)
    model = OllamaLLM(model=model_name, base_url=base_url)
    chain = prompt | model
    return prompt, model, chain


# Module-level instances for direct import
prompt, model, chain = build_rag_chain()
