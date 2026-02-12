from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is online and running!"

def run():
    # Render provides a PORT environment variable automatically
    import os
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
