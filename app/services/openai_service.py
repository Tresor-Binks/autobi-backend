"""
OPENAI SERVICE

Responsabilités :
- Envoyer un résumé compact du dataset à GPT-3.5
- Recevoir les suggestions d'insights (max 6), graphiques et analyses
- Valider les insights personnalisés des utilisateurs
- Parser et valider les réponses JSON
"""

import json
import os
from typing import Optional
from openai import OpenAI


# ============================================================================
# CLIENT OPENAI
# ============================================================================

def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY manquante dans les variables d'environnement")
    return OpenAI(api_key=api_key)


# ============================================================================
# PROMPT SYSTÈME — SUGGESTIONS D'INSIGHTS
# ============================================================================

SYSTEM_PROMPT = """Tu es un expert senior en analyse de données et data visualization. Tu dois analyser un dataset et générer des instructions d'analyse complètes et pertinentes.

RÈGLES ABSOLUES :
1. Réponds UNIQUEMENT avec un JSON valide — zéro texte avant ou après, zéro backtick, zéro markdown
2. Utilise UNIQUEMENT les colonnes listées dans le dataset fourni
3. Adapte tes suggestions au TYPE RÉEL de données (ventes, RH, finance, logistique, etc.)
4. Évite toute redondance : chaque insight et chaque graphique doit apporter une information différente
5. Priorité aux colonnes avec le plus de variance et de sens métier

STRUCTURE DE RÉPONSE OBLIGATOIRE :
{
  "insights": [
    {
      "id": "insight_1",
      "title": "Titre court et précis (max 8 mots)",
      "description": "Explication claire en 1-2 phrases. Ce que cet insight révèle concrètement.",
      "type": "trend|comparison|total|anomaly|distribution",
      "feasibility": "high|medium|low",
      "required_columns": ["col1", "col2"]
    }
  ],
  "charts": [
    {
      "id": "chart_1",
      "type": "line|bar|pie|scatter",
      "title": "Titre descriptif du graphique",
      "x": "nom_exact_colonne_x",
      "y": "nom_exact_colonne_y",
      "aggregation": "sum|mean|count|none",
      "description": "Ce que ce graphique montre et comment l'interpréter"
    }
  ],
  "analyses": [
    {
      "id": "analysis_1",
      "type": "trend|groupby|correlation|distribution|top_n",
      "description": "Description précise de l'analyse",
      "columns": ["col1", "col2"],
      "aggregation": "sum|mean|count"
    }
  ],
  "summary": "Paragraphe de 3-5 phrases qui : 1) décrit le dataset, 2) identifie les tendances clés, 3) propose des recommandations concrètes basées sur les données"
}

RÈGLES PAR TYPE DE GRAPHIQUE :
- line : x = colonne DATE ou catégorie ordonnée, y = colonne NUMÉRIQUE. Idéal pour les tendances temporelles.
- bar : x = colonne CATÉGORIELLE (max 20 valeurs uniques), y = colonne NUMÉRIQUE. Idéal pour les comparaisons.
- pie : x = colonne CATÉGORIELLE (max 8 valeurs), y = colonne NUMÉRIQUE à agréger. Idéal pour les parts de marché.
- scatter : x ET y = colonnes NUMÉRIQUES différentes. Idéal pour les corrélations.

QUALITÉ DES INSIGHTS :
- Propose exactement 4 à 6 insights (jamais moins, jamais plus)
- Chaque insight doit être ACTIONNABLE : "Les ventes chutent le lundi → optimiser les promotions en début de semaine"
- Varie les types : mix de trend, comparison, distribution, anomaly
- feasibility = high si les colonnes existent et sont numériques/catégorielles compatibles

QUALITÉ DU SUMMARY :
- Commence par une description factuelle du dataset
- Identifie 2-3 observations importantes directement lisibles dans les données
- Termine par 1-2 recommandations concrètes basées sur les statistiques fournies
- Écris en français, ton professionnel mais accessible"""


# ============================================================================
# CONSTRUCTION DU MESSAGE UTILISATEUR
# ============================================================================

def build_user_message(sample: dict) -> str:
    """
    Construit un message riche pour OpenAI avec toutes les informations
    nécessaires pour une analyse de qualité.
    """
    col_types = sample.get("column_types", {})
    stats_summary = sample.get("stats_summary", {})
    sample_rows = sample.get("sample_rows", [])[:10]
    numeric_cols = sample.get("numeric_columns", [])
    categorical_cols = sample.get("categorical_columns", [])
    date_cols = sample.get("date_columns", [])
    columns = sample.get("columns", [])
    row_count = sample.get("row_count", 0)

    # Construction du résumé des statistiques
    stats_text = []
    for col, stat in stats_summary.items():
        if isinstance(stat, dict):
            if "mean" in stat:
                stats_text.append(
                    f"  - {col} : moyenne={stat.get('mean')}, min={stat.get('min')}, max={stat.get('max')}, médiane={stat.get('median', 'N/A')}"
                )
            elif "top_values" in stat:
                top = stat.get("top_values", [])[:3]
                stats_text.append(f"  - {col} : {stat.get('unique_count')} valeurs uniques, top: {', '.join(top)}")

    message = f"""DATASET À ANALYSER :

Nombre de lignes : {row_count:,}
Nombre de colonnes : {len(columns)}

COLONNES ET TYPES :
{json.dumps(col_types, ensure_ascii=False, indent=2)}

COLONNES NUMÉRIQUES : {numeric_cols}
COLONNES CATÉGORIELLES : {categorical_cols}
COLONNES DE DATES : {date_cols}

STATISTIQUES DÉTAILLÉES :
{chr(10).join(stats_text) if stats_text else '  Aucune statistique disponible'}

ÉCHANTILLON DES DONNÉES (10 premières lignes) :
{json.dumps(sample_rows, indent=2, ensure_ascii=False, default=str)}

INSTRUCTIONS :
- Génère 4 à 6 insights pertinents et non redondants
- Génère 2 à 4 graphiques adaptés aux types de colonnes
- Génère 2 à 3 analyses Pandas
- Le summary doit expliquer ce que révèlent les données et proposer des pistes d'action
- Réponds UNIQUEMENT avec le JSON, rien d'autre"""

    return message


# ============================================================================
# APPEL OPENAI — SUGGESTIONS D'INSIGHTS
# ============================================================================

def get_analysis_instructions(sample: dict) -> dict:
    """
    Envoie le résumé du dataset à GPT-3.5 et retourne les instructions d'analyse.
    """
    try:
        client = get_openai_client()
        user_message = build_user_message(sample)

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            temperature=0.2,
            max_tokens=2500,
        )

        raw_response = response.choices[0].message.content.strip()
        return parse_openai_response(raw_response)

    except Exception as e:
        return build_fallback_response(sample, str(e))


# ============================================================================
# PARSING DE LA RÉPONSE
# ============================================================================

def parse_openai_response(raw: str) -> dict:
    cleaned = raw.strip()

    # Nettoyage backticks markdown
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]).strip()

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(cleaned[start:end])
            else:
                raise ValueError("Aucun JSON trouvé")
        except Exception:
            return build_fallback_response({}, "Erreur de parsing JSON")

    return validate_and_clean_response(result)


def validate_and_clean_response(result: dict) -> dict:
    result.setdefault("insights", [])
    result.setdefault("charts", [])
    result.setdefault("analyses", [])
    result.setdefault("summary", "")

    # Limite à 6 insights
    result["insights"] = result["insights"][:6]

    for i, insight in enumerate(result["insights"]):
        insight.setdefault("id", f"insight_{i+1}")
        insight.setdefault("title", f"Insight {i+1}")
        insight.setdefault("description", "")
        insight.setdefault("type", "comparison")
        insight.setdefault("feasibility", "medium")
        insight.setdefault("required_columns", [])

    for i, chart in enumerate(result["charts"]):
        chart.setdefault("id", f"chart_{i+1}")
        chart.setdefault("type", "bar")
        chart.setdefault("title", f"Graphique {i+1}")
        chart.setdefault("aggregation", "sum")
        chart.setdefault("description", "")

    for i, analysis in enumerate(result["analyses"]):
        analysis.setdefault("id", f"analysis_{i+1}")
        analysis.setdefault("type", "groupby")
        analysis.setdefault("description", "")
        analysis.setdefault("columns", [])
        analysis.setdefault("aggregation", "sum")

    return result


# ============================================================================
# FALLBACK EN CAS D'ERREUR OPENAI
# ============================================================================

def build_fallback_response(sample: dict, error: str = "") -> dict:
    """
    Génère une réponse de fallback basée sur les colonnes détectées
    si OpenAI est indisponible.
    """
    numeric_cols = sample.get("numeric_columns", [])
    categorical_cols = sample.get("categorical_columns", [])
    date_cols = sample.get("date_columns", [])
    columns = sample.get("columns", [])

    insights = []
    charts = []
    analyses = []

    if date_cols and numeric_cols:
        insights.append({
            "id": "insight_1",
            "title": f"Évolution de {numeric_cols[0]} dans le temps",
            "description": f"Tendance de {numeric_cols[0]} sur la période couverte par {date_cols[0]}.",
            "type": "trend", "feasibility": "high",
            "required_columns": [date_cols[0], numeric_cols[0]]
        })
        charts.append({
            "id": "chart_1", "type": "line",
            "title": f"Évolution de {numeric_cols[0]}",
            "x": date_cols[0], "y": numeric_cols[0],
            "aggregation": "sum",
            "description": f"Tendance de {numeric_cols[0]} au fil du temps."
        })
        analyses.append({
            "id": "analysis_1", "type": "trend",
            "description": f"Tendance de {numeric_cols[0]} par {date_cols[0]}",
            "columns": [date_cols[0], numeric_cols[0]], "aggregation": "sum"
        })

    if categorical_cols and numeric_cols:
        insights.append({
            "id": "insight_2",
            "title": f"Comparaison de {numeric_cols[0]} par {categorical_cols[0]}",
            "description": f"Répartition de {numeric_cols[0]} selon {categorical_cols[0]}.",
            "type": "comparison", "feasibility": "high",
            "required_columns": [categorical_cols[0], numeric_cols[0]]
        })
        charts.append({
            "id": "chart_2", "type": "bar",
            "title": f"{numeric_cols[0]} par {categorical_cols[0]}",
            "x": categorical_cols[0], "y": numeric_cols[0],
            "aggregation": "sum",
            "description": f"Total de {numeric_cols[0]} par {categorical_cols[0]}."
        })
        analyses.append({
            "id": "analysis_2", "type": "groupby",
            "description": f"Agrégation de {numeric_cols[0]} par {categorical_cols[0]}",
            "columns": [categorical_cols[0], numeric_cols[0]], "aggregation": "sum"
        })

    if numeric_cols:
        insights.append({
            "id": "insight_3",
            "title": f"Distribution de {numeric_cols[0]}",
            "description": f"Répartition statistique des valeurs de {numeric_cols[0]}.",
            "type": "distribution", "feasibility": "high",
            "required_columns": [numeric_cols[0]]
        })

    if len(numeric_cols) >= 2:
        insights.append({
            "id": "insight_4",
            "title": f"Corrélation {numeric_cols[0]} / {numeric_cols[1]}",
            "description": f"Relation entre {numeric_cols[0]} et {numeric_cols[1]}.",
            "type": "comparison", "feasibility": "medium",
            "required_columns": [numeric_cols[0], numeric_cols[1]]
        })
        charts.append({
            "id": "chart_3", "type": "scatter",
            "title": f"{numeric_cols[0]} vs {numeric_cols[1]}",
            "x": numeric_cols[0], "y": numeric_cols[1],
            "aggregation": "none",
            "description": f"Corrélation entre {numeric_cols[0]} et {numeric_cols[1]}."
        })

    return {
        "insights": insights[:6],
        "charts": charts,
        "analyses": analyses,
        "summary": f"Analyse automatique générée (OpenAI indisponible : {error}). "
                   f"Dataset de {sample.get('row_count', 0)} lignes et {len(columns)} colonnes."
    }


# ============================================================================
# VALIDATION D'UN INSIGHT PERSONNALISÉ
# ============================================================================

VALIDATE_INSIGHT_PROMPT = """Tu es un expert en analyse de données.
Tu reçois une demande d'insight en langage naturel et la liste des colonnes disponibles.

Retourne UNIQUEMENT un JSON valide (sans markdown) avec cette structure :
{
  "valid": true | false,
  "reformulated_title": "Titre court et clair (si valid=true)",
  "reformulated_description": "Description précise reformulée (si valid=true)",
  "required_columns": ["col1", "col2"],
  "type": "trend|comparison|total|anomaly|distribution",
  "feasibility": "high|medium|low",
  "reason": "Explication si non réalisable (null si valid=true)"
}

Règles :
- Interprète la demande avec bienveillance, même si elle est vague ou mal formulée
- valid=true si réalisable avec les colonnes disponibles
- valid=false si les données nécessaires n'existent pas, explique clairement pourquoi
- Ne demande PAS à l'utilisateur de mentionner des noms de colonnes exacts
- Réponds UNIQUEMENT avec le JSON"""


def validate_custom_insight(description: str, columns: list, column_types: dict) -> dict:
    """
    Valide et reformule un insight personnalisé via OpenAI.
    """
    try:
        client = get_openai_client()
    except ValueError as e:
        return {
            "valid": False, "reformulated_title": None,
            "reformulated_description": None, "required_columns": [],
            "type": "comparison", "feasibility": "low",
            "reason": None,
            "error": "Service d'analyse indisponible. Veuillez réessayer plus tard."
        }

    user_message = f"""Demande de l'utilisateur : "{description}"

Colonnes disponibles :
{json.dumps({col: column_types.get(col, 'text') for col in columns}, ensure_ascii=False, indent=2)}

Vérifie si cet insight est réalisable et reformule-le si nécessaire."""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": VALIDATE_INSIGHT_PROMPT},
                {"role": "user", "content": user_message}
            ],
            temperature=0.2,
            max_tokens=500,
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]).strip()

        result = json.loads(raw)
        result.setdefault("valid", False)
        result.setdefault("reformulated_title", None)
        result.setdefault("reformulated_description", None)
        result.setdefault("required_columns", [])
        result.setdefault("type", "comparison")
        result.setdefault("feasibility", "medium")
        result.setdefault("reason", None)
        result["error"] = None
        return result

    except Exception as e:
        error_msg = str(e)
        if "quota" in error_msg.lower() or "429" in error_msg:
            msg = "Service d'analyse temporairement indisponible."
        elif "connection" in error_msg.lower():
            msg = "Connexion au service impossible. Vérifiez votre connexion internet."
        else:
            msg = "Service d'analyse indisponible. Veuillez réessayer plus tard."

        return {
            "valid": False, "reformulated_title": None,
            "reformulated_description": None, "required_columns": [],
            "type": "comparison", "feasibility": "low",
            "reason": None, "error": msg
        }