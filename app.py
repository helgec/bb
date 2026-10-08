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
    origin_channel_id = body["channel_id"]

    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "breaking_modal",
            "private_metadata": origin_channel_id,
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
    origin_channel_id = view.get("private_metadata")

    if (new_channel and existing_channel) or (not new_channel and not existing_channel):
        ack(response_action="errors", errors={
            "new_channel_block": "Du må fylle ut ETT av feltene (nytt navn eller eksisterende kanal)."
        })
        return

    ack()

    try:
        # A. Håndter opprettelse eller tilkobling til kanal
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

        # B. Samle opp alle brukere som skal ha tilgang
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

        # C. INVITÉR BRUKERE TIL KANALEN FØRST
        if all_to_invite:
            try:
                users_string = ",".join(all_to_invite)
                client.conversations_invite(channel=channel_id, users=users_string)
            except Exception as e:
                print(f"Advarsel: Kunne ikke invitere brukere direkte til kanalen: {e}")

        # D. Hent ekte navn på reportasjeleder
        leader_display_name = f"<@{leader}>"
        try:
            user_info = client.users_info(user=leader)
            profile = user_info.get("user", {}).get("profile", {})
            real_name = profile.get("real_name") or profile.get("display_name") or user_info.get("user", {}).get("name")
            if real_name:
                leader_display_name = real_name
        except Exception as e:
            print(f"Kunne ikke slå opp visningsnavn for leder: {e}")

        team_id = body["team"]["id"]

        # Filtrer kun ut gyldige bruker-ID-er (starter på U eller W) for listetilgang
        valid_user_ids = [u for u in all_to_invite if u.startswith("U") or u.startswith("W")]

        # E. Opprett Kildeoversikt-liste & gi tilgang
        kilde_list_url = ""
        try:
            kilde_res = client.api_call(
                api_method="slackLists.create",
                json={
                    "name": "Kildeoversikt",
                    "schema": [
                        {"key": "kilde", "name": "Kilde", "type": "text", "is_primary_column": True},
                        {"key": "siste_kontakt", "name": "Siste kontakt", "type": "date"},
                        {"key": "ansvarlig", "name": "Ansvarlig", "type": "user"},
                        {"key": "kontaktinfo", "name": "Kontaktinfo", "type": "text"},
                        {"key": "kommentar", "name": "Kommentar", "type": "text"}
                    ]
                }
            )
            kilde_id = kilde_res.get("list_id") or kilde_res.get("list", {}).get("id")
            if kilde_id:
                kilde_list_url = f"https://app.slack.com/lists/{team_id}/{kilde_id}"
                
                # 1. Gi tilgang til hele kanalen
                access_chan_res = client.api_call(
                    api_method="slackLists.access.set",
                    json={
                        "list_id": kilde_id,
                        "access_level": "write",
                        "channel_ids": [channel_id]
                    }
                )
                print(f"DEBUG - Kildeoversikt kanaltilgang: {access_chan_res}")

                # 2. Gi tilgang til spesifikke brukere
                if valid_user_ids:
                    access_user_res = client.api_call(
                        api_method="slackLists.access.set",
                        json={
                            "list_id": kilde_id,
                            "access_level": "write",
                            "user_ids": valid_user_ids
                        }
                    )
                    print(f"DEBUG - Kildeoversikt brukertilgang: {access_user_res}")

                try:
                    client.bookmarks_add(
                        channel_id=channel_id,
                        title="Kildeoversikt",
                        type="link",
                        link=kilde_list_url
                    )
                except Exception as e:
                    print(f"Advarsel: Kunne ikke feste Kildeoversikt som fane: {e}")
        except Exception as e:
            print(f"FEIL VED OPPRETTELSE AV KILDEOVERSIKT: {e}")

        # F. Opprett Verifisering-liste & gi tilgang
        verifisering_list_url = ""
        try:
            verif_res = client.api_call(
                api_method="slackLists.create",
                json={
                    "name": "Verifisering",
                    "schema": [
                        {"key": "url", "name": "URL", "type": "text", "is_primary_column": True},
                        {"key": "verifisert", "name": "Verifisert?", "type": "checkbox"},
                        {"key": "verifisert_av", "name": "Verifisert av", "type": "user"},
                        {"key": "kommentar", "name": "Kommentar", "type": "text"},
                        {"key": "potionlenke", "name": "Potionlenke", "type": "text"}
                    ]
                }
            )
            verif_id = verif_res.get("list_id") or verif_res.get("list", {}).get("id")
            if verif_id:
                verifisering_list_url = f"https://app.slack.com/lists/{team_id}/{verif_id}"
                
                # 1. Gi tilgang til hele kanalen
                access_chan_res = client.api_call(
                    api_method="slackLists.access.set",
                    json={
                        "list_id": verif_id,
                        "access_level": "write",
                        "channel_ids": [channel_id]
                    }
                )
                print(f"DEBUG - Verifisering kanaltilgang: {access_chan_res}")

                # 2. Gi tilgang til spesifikke brukere
                if valid_user_ids:
                    access_user_res = client.api_call(
                        api_method="slackLists.access.set",
                        json={
                            "list_id": verif_id,
                            "access_level": "write",
                            "user_ids": valid_user_ids
                        }
                    )
                    print(f"DEBUG - Verifisering brukertilgang: {access_user_res}")

                try:
                    client.bookmarks_add(
                        channel_id=channel_id,
                        title="Verifisering",
                        type="link",
                        link=verifisering_list_url
                    )
                except Exception as e:
                    print(f"Advarsel: Kunne ikke feste Verifisering som fane: {e}")
        except Exception as e:
            print(f"FEIL VED OPPRETTELSE AV VERIFISERINGSLISTE: {e}")

        # G. Opprett Channel Canvas (Arbeidsliste)
        try:
            kilde_punkt = f"* [📋 Gå til Kildeoversikt]({kilde_list_url})" if kilde_list_url else "* _Kunne ikke opprette Kildeoversikt automatisk._"
            verif_punkt = f"* [🔍 Gå til Verifisering]({verifisering_list_url})" if verifisering_list_url else "* _Kunne ikke opprette Verifiseringsliste automatisk._"

            canvas_markdown = textwrap.dedent(f"""\
                # 🚨 Sakslogg

                ## ❓ Hvem, hva, hvor?
                *
                *
                *

                ### 👥 Roller
                * **Reportasjeleder:** {leader_display_name}
                * **Rykk:** 
                * **Hovedmanus:**

                ### 📌 Ubekrefta informasjon
                *
                *
                *

                ### 📞 Viktige kontakter & kilder
                {kilde_punkt}
                {verif_punkt}

                ### 🔗 Lenker og dokumenter
                * 
                """).strip()

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

# H. Melding i den nye/eksisterende kanalen (med stor overskrift)
        kilde_str = f"\n📊 *Kildeoversikt:* <{kilde_list_url}|Trykk her for å åpne Listen>" if kilde_list_url else ""
        verif_str = f"\n🔍 *Verifisering:* <{verifisering_list_url}|Trykk her for å åpne Listen>" if verifisering_list_url else ""

        client.chat_postMessage(
            channel=channel_id,
            text=f"Velkommen til kanalen! Ansvarlig reportasjeleder er <@{leader}>.",  # Vises i push-varsel
            unfurl_links=False,
            unfurl_media=False,
            blocks=[
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🚨 Velkommen til kanalen",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"Ansvarlig reportasjeleder er <@{leader}>.\n\n"
                            f"📝 *Bruk canvaset (Arbeidsliste) øverst i fane-menyen.*"
                            f"{kilde_str}{verif_str}\n\n"
                            f"⏱️ *Husk at denne kanalen _skal_ settes til privat om 15 minutter.*"
                        )
                    }
                }
            ]
        )

        # I. Varsling i kanalen der kommandoen ble startet fra
        if origin_channel_id:
            try:
                client.chat_postMessage(
                    channel=origin_channel_id,
                    text=f"🚨 *Ny breaking-kanal opprettet!*\n<@{user_id}> har opprettet/koblet opp <#{channel_id}>. Ansvarlig reportasjeleder: <@{leader}>.\n\n👉 Trykk på <#{channel_id}> for å gå til kanalen og bli med."
                )
            except Exception as e:
                print(f"Kunne ikke sende melding til starter-kanal: {e}")

        # J. Varsling i felleskanal via .env
        varsling_kanal = os.environ.get("VARSLING_CHANNEL_ID")
        if varsling_kanal and varsling_kanal != origin_channel_id:
            try:
                client.chat_postMessage(
                    channel=varsling_kanal,
                    text=f"<@{user_id}> har opprettet/koblet opp <#{channel_id}>. Ansvarlig reportasjeleder: <@{leader}>."
                )
            except Exception as e:
                print(f"Kunne ikke sende varsel til felleskanal: {e}")

        # K. Start 15-minutters timer (900 sekunder)
        remind_to_make_private(client, channel_id, user_id, delay_seconds=900)

    except Exception as e:
        print(f"Feil i prosesseringen: {e}")

if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()
