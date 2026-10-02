import os

def create_file(filename, content):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        return f"✅ File '{filename}' created successfully"
    except Exception as e:
        return f"❌ Error creating file: {e}"