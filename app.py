import os
import threading
import time
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

# 1. Vis skjema når noen bruker /breaking
@app.command("/breaking")
def open_breaking_modal(ack, body, client):
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "breaking_modal",
            "title": {"type": "plain_text", "text": "Ny Breaking-kanal"},
            "submit": {"type": "plain_text", "text": "Start prosess"},
            "close": {"type": "plain_text", "text": "Avbryt"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "new_channel_block",
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "new_channel_input",
                        "placeholder": {"type": "plain_text", "text": "f.eks. breaking-togavsporing"}
                    },
                    "label": {"type": "plain_text", "text": "Skriv nytt kanalnavn"}
                },
                {
                    "type": "input",
                    "block_id": "existing_channel_block",
                    "optional": True,
                    "element": {
                        "type": "channels_select",
                        "action_id": "existing_channel_input",
                        "placeholder": {"type": "plain_text", "text": "Velg fra listen..."}
                    },
                    "label": {"type": "plain_text", "text": "ELLER velg en eksisterende kanal"}
                },
                {
                    "type": "input",
                    "block_id": "leader_block",
                    "element": {
                        "type": "users_select",
                        "action_id": "leader_input",
                        "placeholder": {"type": "plain_text", "text": "Velg leder..."}
                    },
                    "label": {"type": "plain_text", "text": "Ansvarlig reportasjeleder"}
                },
                {
                    "type": "input",
                    "block_id": "users_block",
                    "element": {
                        "type": "multi_users_select",
                        "action_id": "users_input",
                        "placeholder": {"type": "plain_text", "text": "Velg kolleger..."}
                    },
                    "label": {"type": "plain_text", "text": "Inviter kolleger"}
                }
            ]
        }
    )

# Funksjon for å gjøre kanalen privat etter 15 minutter (kjører i bakgrunnen)
def convert_to_private_later(client, channel_id, delay_seconds=900):
    def task():
        time.sleep(delay_seconds)
        try:
            client.conversations_convertToPrivate(channel=channel_id)
            print(f"Kanal {channel_id} ble automatisk satt til privat.")
        except Exception as e:
            print(f"Feilet under konvertering til privat: {e}")

    threading.Thread(target=task, daemon=True).start()

# 2. Håndter at skjemaet sendes inn
@app.view("breaking_modal")
def handle_modal_submit(ack, body, client, view):
    values = view["state"]["values"]
    new_channel = values["new_channel_block"]["new_channel_input"].get("value")
    existing_channel = values["existing_channel_block"]["existing_channel_input"].get("selected_channel")
    leader = values["leader_block"]["leader_input"]["selected_user"]
    invited_users = values["users_block"]["users_input"]["selected_users"]
    user_id = body["user"]["id"]

    # Validering: Bruker må fylle ut Enten ny ELLER eksisterende kanal
    if (new_channel and existing_channel) or (not new_channel and not existing_channel):
        ack(response_action="errors", errors={
            "new_channel_block": "Du må fylle ut ETT av feltene (nytt navn eller eksisterende kanal)."
        })
        return

    ack()

try:
        # A. Håndter kanal
        if new_channel:
            # 1. Fjern eventuell '#' i starten og tomrom rundt
            clean_name = new_channel.strip().lstrip("#")
            
            # 2. Gjør om til små bokstaver og erstatt norske tegn
            clean_name = clean_name.lower().replace("æ", "ae").replace("ø", "o").replace("å", "a")
            
            # 3. Erstatt ulovlige tegn/mellomrom med bindestrek
            clean_name = re.sub(r'[^a-z0-9-_]', '-', clean_name)
            clean_name = re.sub(r'-+', '-', clean_name).strip('-')
            
            # 4. Opprett kanalen med det vaskede navnet
            res = client.conversations_create(name=clean_name)
            channel_id = res["channel"]["id"]
        else:
            channel_id = existing_channel
            try:
                client.conversations_join(channel=channel_id)
            except Exception:
                pass

        # B. Inviter brukere
        all_to_invite = set(invited_users + [leader, user_id])
        for u in all_to_invite:
            try:
                client.conversations_invite(channel=channel_id, users=u)
            except Exception:
                pass  # Ignorer om bruker allerede er i kanalen

        # C. Send velkomstmelding i breaking-kanalen
        client.chat_postMessage(
            channel=channel_id,
            text=f"Velkommen til kanalen! Ansvarlig reportasjeleder er <@{leader}>.\n*Denne kanalen settes automatisk til privat om 15 minutter.*"
        )

        # D. Send varsling i felleskanal (bytt ut 'akt-ny-sakskanal' med kanal-ID eller navn hos dere)
        client.chat_postMessage(
            channel="akt-ny-sakskanal",
            text=f"<@{user_id}> har opprettet/koblet opp <#{channel_id}>. Ansvarlig reportasjeleder: <@{leader}>."
        )

        # E. Start bakgrunnstimer på 15 minutter (900 sekunder)
        convert_to_private_later(client, channel_id, delay_seconds=900)

    except Exception as e:
        print(f"Feil i prosesseringen: {e}")

if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()
