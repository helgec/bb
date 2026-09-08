import os
import re
import textwrap
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
    origin_channel_id = body["channel_id"]  # Henter ID til kanalen kommandoen ble startet i

    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "breaking_modal",
            "private_metadata": origin_channel_id,  # Lagrer kanal-ID-en i skjemaet
            "title": {"type": "plain_text", "text": "Ny Breaking-kanal"},
            "submit": {"type": "plain_text", "text": "Start prosess"},
            "close": {"type": "plain_text", "text": "Avbryt"},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Fyll ut skjemaet under for å sette opp en breaking-kanal. _Automatiske invitasjoner sendes i bakgrunnen._"
                    }
                },
                {"type": "divider"},
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "📺 1. Velg eller lag kanal", "emoji": True}
                },
                {
                    "type": "input",
                    "block_id": "new_channel_block",
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "new_channel_input",
                        "placeholder": {"type": "plain_text", "text": "f.eks. sak-togavsporing"}
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
                {"type": "divider"},
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "👥 2. Bemanning", "emoji": True}
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
                    "optional": True,
                    "element": {
                        "type": "multi_users_select",
                        "action_id": "users_input",
                        "placeholder": {"type": "plain_text", "text": "Velg kolleger..."}
                    },
                    "label": {"type": "plain_text", "text": "Inviter kolleger (valgfritt)"}
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "ℹ️ *Tips:* Faste grupper og ledere blir automatisk invitert, så du trenger bare legge til de som trengs spesifikt for denne saken."
                        }
                    ]
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
    invited_users = values["users_block"]["users_input"].get("selected_users", [])
    user_id = body["user"]["id"]
    origin_channel_id = view.get("private_metadata")  # Henter ut opprinnelig kanal-ID

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
            clean_name = clean_name.lower().replace("æ", "a").replace("ø", "o").replace("å", "a")
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

        # B1. Opprett standard Channel Canvas Uten Tabell
        try:
            canvas_markdown = textwrap.dedent(f"""\
                ### 👥 Roller
                * **Reportasjeleder:** ![](@{leader})
                * **Reporter:** _Skriv navn_

                ### 🔗 Lenker og dokumenter
                * (Lim inn lenker her)""").strip()

            client.conversations_canvases_create(
                channel_id=channel_id,
                title="Arbeidsliste",
                document_content={
                    "type": "markdown",
                    "markdown": canvas_markdown
                }
            )
        except Exception as e:
            print(f"Advarsel: Kunne ikke opprette canvas: {e}")

        # B2. Opprett Slack List (Kildeoversikt)
        try:
            list_res = client.api_call(
                api_method="lists.create",
                json={
                    "title": "Kildeoversikt",
                    "channel_id": channel_id,
                    "schema": [
                        {"name": "Kilde", "type": "text"},
                        {"name": "Siste kontakt", "type": "date"},
                        {"name": "Ansvarlig", "type": "user"},
                        {"name": "Kontaktinfo", "type": "text"},
                        {"name": "Kommentar", "type": "text"}
                    ]
                }
            )
            
            list_id = list_res.get("list", {}).get("id")

            # Generer 5 tomme rader i listen
            if list_id:
                for _ in range(5):
                    client.api_call(
                        api_method="lists.items.create",
                        json={
                            "list_id": list_id,
                            "fields": {}
                        }
                    )
        except Exception as e:
            print(f"Advarsel: Kunne ikke opprette liste: {e}")

        # C. Inviter brukere
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

        # D. Melding i den nye/eksisterende kanalen (Oppdatert tekst)
        client.chat_postMessage(
            channel=channel_id,
            text=f"Velkommen til kanalen! Ansvarlig reportasjeleder er <@{leader}>.\n\n📝 *Jeg har lagt opp et Canvas (Arbeidsliste) og en egen Slack-liste (Kildeoversikt) for å lette arbeidet. Du finner begge to i fanene øverst i kanalen!*\n\n*Husk at denne kanalen skal settes til privat om 15 minutter.*"
        )

        # E. Varsling i kanalen der kommandoen ble startet fra
        if origin_channel_id:
            try:
                client.chat_postMessage(
                    channel=origin_channel_id,
                    text=f"🚨 *Ny breaking-kanal opprettet!*\n<@{user_id}> har opprettet/koblet opp <#{channel_id}>. Ansvarlig reportasjeleder: <@{leader}>.\n\n👉 Trykk på <#{channel_id}> for å gå til kanalen og bli med."
                )
            except Exception as e:
                print(f"Kunne ikke sende melding til starter-kanal: {e}")

        # F. Varsling i felleskanal via .env (hvis definert og ulik starter-kanalen)
        varsling_kanal = os.environ.get("VARSLING_CHANNEL_ID")
        if varsling_kanal and varsling_kanal != origin_channel_id:
            try:
                client.chat_postMessage(
                    channel=varsling_kanal,
                    text=f"<@{user_id}> har opprettet/koblet opp <#{channel_id}>. Ansvarlig reportasjeleder: <@{leader}>."
                )
            except Exception as e:
                print(f"Kunne ikke sende varsel til felleskanal: {e}")

        # G. Start 15-minutters timer (900 sekunder)
        remind_to_make_private(client, channel_id, user_id, delay_seconds=900)

    except Exception as e:
        print(f"Feil i prosesseringen: {e}")

if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()
