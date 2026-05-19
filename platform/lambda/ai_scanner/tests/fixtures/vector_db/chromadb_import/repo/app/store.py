import chromadb

client = chromadb.PersistentClient(path="/tmp/chroma")
collection = client.get_or_create_collection("docs")
