import boto3
import json

client = boto3.client("bedrock-runtime")
resp = client.invoke_model(
    modelId="anthropic.claude-3-sonnet-20240229-v1:0",
    body=json.dumps({"prompt": "hi"}),
)
