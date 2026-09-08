import os
import re
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

# 2. Funksjon for bakgrunnstimer og DM-påminnelse med knapp
def remind_to_make_private(client, channel_id, user_id, delay_seconds=900):
    def task():
        time.sleep(delay_seconds)
        try:
            client.chat_postMessage(
                channel=user_id,
                text=f"Påminnelse: Husk å gjøre <#{channel_id}> privat!",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"⏱️ *Nå har det gått 15 minutter!*\nHusk å sette <#{channel_id}> til privat.\n\n1. Trykk på kanalnavnet øverst\n2. Velg *Settings*\n3. Trykk på *Change to a private channel*"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "✅ Jeg har gjort den privat"},
                                "style": "primary",
                                "action_id": "mark_private_done"
                            }
                        ]
                    }
                ]
            )
        except Exception as e:
            print(f"Feilet under sending av påminnelse: {e}")

    threading.Thread(target=task, daemon=True).start()

# 3. Håndter trykk på "Jeg har gjort den privat"-knappen
@app.action("mark_private_done")
def handle_mark_private_done(ack, body, client):
    ack()
    client.chat_update(
        channel=body["channel"]["id"],
        ts=body["message"]["ts"],
        text="Takk! Registrert at kanalen er satt til privat.",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "✅ *Takk!* Du har bekreftet at kanalen er satt til privat."
                }
            }
        ]
    )

# 4. Håndter at skjemaet sendes inn
@app.view("breaking_modal")
def handle_modal_submit(ack, body, client, view):
    values = view["state"]["values"]
    new_channel = values["new_channel_block"]["new_channel_input"].get("value")
    existing_channel = values["existing_channel_block"]["existing_channel_input"].get("selected_channel")
    leader = values["leader_block"]["leader_input"]["selected_user"]
    invited_users = values["users_block"]["users_input"]["selected_users"]
    user_id = body["user"]["id"]

    if (new_channel and existing_channel) or (not new_channel and not existing_channel):
        ack(response_action="errors", errors={
            "new_channel_block": "Du må fylle ut ETT av feltene (nytt navn eller eksisterende kanal)."
        })
        return

    ack()

    try:
        # A. Håndter kanal
        if new_channel:
            clean_name = new_channel.strip().lstrip("#")
            clean_name = clean_name.lower().replace("æ", "ae").replace("ø", "o").replace("å", "a")
            clean_name = re.sub(r'[^a-z0-9-_]', '-', clean_name)
            clean_name = re.sub(r'-+', '-', clean_name).strip('-')

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

        # Hent faste enkeltbrukere fra .env
        auto_users_string = os.environ.get("AUTO_INVITE_USER_ID")
        if auto_users_string:
            auto_users = [u.strip() for u in auto_users_string.split(",")]
            for u_id in auto_users:
                if u_id:
                    all_to_invite.add(u_id)

        # Hent grupper fra .env
        group_ids_string = os.environ.get("AUTO_INVITE_GROUP_ID")
        if group_ids_string:
            group_ids = [g.strip() for g in group_ids_string.split(",")]
            for g_id in group_ids:
                if not g_id:
                    continue
                try:
                    group_response = client.usergroups_users_list(usergroup=g_id)
                    group_members = group_response.get("users", [])
                    all_to_invite.update(group_members)
                except Exception as e:
                    print(f"Advarsel: Kunne ikke hente gruppe {g_id}. Feil: {e}")

        # Utfør selve invitasjonen for alle på listen i én operasjon
        for u in all_to_invite:
            try:
                client.conversations_invite(channel=channel_id, users=u)
            except Exception:
                pass

        # C. Melding i ny kanal
        client.chat_postMessage(
            channel=channel_id,
            text=f"Velkommen til kanalen! Ansvarlig reportasjeleder er <@{leader}>.\n*Kanalen skal settes til privat om 15 minutter.*"
        )

        # D. Varsling i felleskanal via .env
        varsling_kanal = os.environ.get("VARSLING_CHANNEL_ID")
        if varsling_kanal:
            try:
                client.chat_postMessage(
                    channel=varsling_kanal,
                    text=f"<@{user_id}> har opprettet/koblet opp <#{channel_id}>. Ansvarlig reportasjeleder: <@{leader}>."
                )
            except Exception as e:
                print(f"Kunne ikke sende varsel til felleskanal: {e}")

        # E. Start 15-minutters timer (900 sekunder)
        remind_to_make_private(client, channel_id, user_id, delay_seconds=900)

    except Exception as e:
        print(f"Feil i prosesseringen: {e}")

if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()
