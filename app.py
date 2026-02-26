from flask import Flask, request
import requests
import os

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("RECEIVED:", data)

    if data and data.get("status") == "COMPLETED":

        user_id = data.get("metadata", {}).get("user_id")
        amount = data.get("amount")

        if user_id:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": user_id,
                    "text": f"✅ Payment Successful!\nAmount: {amount} BDT"
                }
            )

    return "OK", 200

@app.route("/")
def home():
    return "Webhook Running!"
