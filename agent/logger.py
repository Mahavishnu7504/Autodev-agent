logs = []

def log(message: str):
    print(message)  # terminal
    logs.append(message)

def get_logs():
    return logs

def clear_logs():
    global logs
    logs = []