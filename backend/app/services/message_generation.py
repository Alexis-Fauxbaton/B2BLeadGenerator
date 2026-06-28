"""Génération des 4 variantes de messages de contact.

Si OPENAI_API_KEY est défini dans l'environnement, on tente une génération via
l'API OpenAI. Sinon (ou en cas d'erreur), on retombe sur des templates locaux
propres et personnalisés.
"""
import json
import os
from dataclasses import dataclass
from typing import List, Optional

from ..models import Opportunity, Settings


@dataclass
class MessageSet:
    instagram_dm: str
    email: str
    linkedin: str
    call_script: str
    source: str  # "openai" ou "template"


def _join(items: Optional[List[str]]) -> str:
    items = items or []
    return ", ".join(items) if items else "aménagement et ambiance"


def _context(opp: Opportunity, settings: Settings) -> dict:
    return {
        "name": opp.establishment_name,
        "type": opp.establishment_type,
        "city": opp.city,
        "signal": opp.main_signal,
        "secondary": ", ".join(opp.secondary_signals or []),
        "timing": opp.estimated_timing,
        "needs": _join(opp.probable_needs),
        "decision_maker": opp.decision_maker or "l'équipe dirigeante",
        "provider_name": settings.provider_name,
        "provider_offer": settings.provider_offer,
        "tone": settings.tone,
        "area": settings.target_area,
    }


# --- Templates locaux ---------------------------------------------------------


def generate_with_templates(opp: Opportunity, settings: Settings) -> MessageSet:
    c = _context(opp, settings)

    instagram_dm = (
        f"Bonjour, j'ai vu que vous préparez « {c['name']} » à {c['city']} "
        f"(signal : {c['signal']}). Votre concept a l'air très prometteur. "
        f"Chez {c['provider_name']}, on accompagne les {c['type']}s sur "
        f"{c['provider_offer']}. Vous avez déjà avancé sur {c['needs']} pour cette étape ?"
    )

    email = (
        f"Objet : {c['name']} — accompagnement {c['needs']}\n\n"
        f"Bonjour,\n\n"
        f"Je me permets de vous contacter au sujet de « {c['name']} » à {c['city']}. "
        f"Nous avons repéré un signal de {c['signal']} (échéance estimée {c['timing']}), "
        f"souvent le bon moment pour anticiper les besoins en {c['needs']}.\n\n"
        f"Chez {c['provider_name']}, nous proposons {c['provider_offer']}. "
        f"Nous accompagnons des établissements comparables au vôtre, de la sélection "
        f"jusqu'à la mise en place.\n\n"
        f"Seriez-vous disponible pour un court échange cette semaine ?\n\n"
        f"Bien à vous,\nL'équipe {c['provider_name']}"
    )

    linkedin = (
        f"Bonjour, je suis tombé sur le projet « {c['name']} » à {c['city']} "
        f"({c['signal']}). Félicitations pour cette étape. Je m'adresse à "
        f"{c['decision_maker']} : chez {c['provider_name']}, nous accompagnons les "
        f"{c['type']}s sur {c['provider_offer']}. Si {c['needs']} fait partie de vos "
        f"sujets d'ici {c['timing']}, je serais ravi d'échanger. Bonne journée."
    )

    call_script = (
        f"SCRIPT D'APPEL — {c['name']} ({c['city']})\n"
        f"Contexte : {c['signal']} | échéance {c['timing']} | besoin probable : {c['needs']}\n\n"
        f"1. Accroche : « Bonjour, je vous appelle au sujet de {c['name']}. "
        f"J'ai vu que vous étiez en phase de {c['signal']}, c'est bien ça ? »\n"
        f"2. Valeur : « Chez {c['provider_name']}, on accompagne les {c['type']}s sur "
        f"{c['provider_offer']}. »\n"
        f"3. Question d'ouverture : « Où en êtes-vous sur {c['needs']} ? »\n"
        f"4. Proposition : « Je peux vous envoyer quelques références adaptées à "
        f"{c['city']}, ou passer vous voir. Qu'est-ce qui vous arrange ? »\n"
        f"5. Closing : caler un rendez-vous ou un envoi de documentation."
    )

    return MessageSet(
        instagram_dm=instagram_dm,
        email=email,
        linkedin=linkedin,
        call_script=call_script,
        source="template",
    )


# --- Génération OpenAI --------------------------------------------------------


def generate_with_openai(opp: Opportunity, settings: Settings) -> Optional[MessageSet]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    c = _context(opp, settings)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    system = (
        "Tu es un assistant commercial B2B spécialisé dans le secteur CHR "
        "(cafés, hôtels, restaurants). Tu rédiges des messages de prospection "
        f"au ton {c['tone']}. Les messages doivent être personnalisés, directs, "
        "non génériques, et adaptés au canal. Réponds STRICTEMENT en JSON."
    )
    user = (
        f"Fournisseur : {c['provider_name']} — {c['provider_offer']}.\n"
        f"Zone ciblée : {c['area']}.\n\n"
        f"Établissement : {c['name']} ({c['type']}) à {c['city']}.\n"
        f"Signal principal : {c['signal']}. Signaux secondaires : {c['secondary']}.\n"
        f"Échéance estimée : {c['timing']}. Besoin probable : {c['needs']}.\n"
        f"Décideur : {c['decision_maker']}.\n\n"
        "Génère 4 messages de prospection dans cet objet JSON exact :\n"
        "{\n"
        '  "instagram_dm": "DM Instagram court et chaleureux",\n'
        '  "email": "email professionnel avec une ligne Objet: en première ligne",\n'
        '  "linkedin": "message LinkedIn adressé au décideur",\n'
        '  "call_script": "script d\'appel structuré en étapes"\n'
        "}"
    )

    try:
        client = OpenAI(api_key=api_key)
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        data = json.loads(completion.choices[0].message.content)
        return MessageSet(
            instagram_dm=data.get("instagram_dm", "").strip(),
            email=data.get("email", "").strip(),
            linkedin=data.get("linkedin", "").strip(),
            call_script=data.get("call_script", "").strip(),
            source="openai",
        )
    except Exception:
        # En cas d'échec (quota, réseau, parsing...), on retombera sur les templates.
        return None


# --- Point d'entrée -----------------------------------------------------------


def generate_messages(opp: Opportunity, settings: Settings) -> MessageSet:
    result = generate_with_openai(opp, settings)
    if result and result.instagram_dm:
        return result
    return generate_with_templates(opp, settings)
