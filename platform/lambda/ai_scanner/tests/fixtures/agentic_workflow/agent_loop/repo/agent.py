from openai import OpenAI

client = OpenAI()
TOOLS = []


def run_agent(prompt):
    messages = [{"role": "user", "content": prompt}]
    while True:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=TOOLS,
        )
        choice = response.choices[0].message
        if not choice.tool_calls:
            return choice.content
        for tool_call in choice.tool_calls:
            messages.append({"role": "tool", "content": "ok"})
