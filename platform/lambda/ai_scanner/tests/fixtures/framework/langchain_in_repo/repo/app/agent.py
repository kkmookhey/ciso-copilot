from langchain.chains import LLMChain
from langchain.llms import OpenAI

chain = LLMChain(llm=OpenAI(), prompt="hello")
