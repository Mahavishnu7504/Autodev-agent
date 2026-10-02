from chromadb import Client
from chromadb.config import Settings

client = Client(Settings(persist_directory="./memory"))

collection = client.get_or_create_collection("autodev")


def save_memory(task, plan):
    collection.add(
        documents=[plan],
        metadatas=[{"task": task}],
        ids=[task]
    )


def search_memory(task):
    results = collection.query(
        query_texts=[task],
        n_results=1
    )

    if results["documents"] and len(results["documents"][0]) > 0:
        return results["documents"][0][0]

    return None