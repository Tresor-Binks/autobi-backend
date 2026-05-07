"""
OPENAI SERVICE

1. get_analysis_instructions(sample)  — pendant l'upload → suggestions d'insights
2. generate_final_report(...)         — après /confirm → rapport JSON complet
"""

import json
import os
from openai import OpenAI


def get_openai_client() -> OpenAI:
    api_key = None
    try:
        from app.core.config import settings
        api_key = getattr(settings, "OPENAI_API_KEY", None)
    except Exception:
        pass
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY manquante — ajoutez-la dans votre fichier .env")
    return OpenAI(api_key=api_key, timeout=180.0)


# ============================================================================
# APPEL 1 — SUGGESTIONS D'INSIGHTS (pendant l'upload)
# ============================================================================

SUGGEST_SYSTEM_PROMPT = """Tu es un expert senior en analyse de données et business intelligence.
Tu reçois un résumé d'un dataset et tu dois suggérer des insights pertinents à analyser.

RÈGLES :
- Réponds UNIQUEMENT avec un JSON valide — zéro texte, zéro markdown, zéro backtick
- Utilise UNIQUEMENT les colonnes listées
- Propose exactement 4 à 6 insights variés, actionnables et non redondants

STRUCTURE :
{
  "insights": [
    {
      "id": "insight_1",
      "title": "Titre court (max 8 mots)",
      "description": "Ce que cet insight révèle concrètement (1-2 phrases).",
      "type": "trend|comparison|total|anomaly|distribution",
      "feasibility": "high|medium|low",
      "required_columns": ["col1", "col2"]
    }
  ],
  "summary": "Paragraphe de 3-5 phrases : description du dataset, tendances clés, recommandations."
}"""


def get_analysis_instructions(sample: dict) -> dict:
    client = get_openai_client()
    stats_lines = []
    for col, stat in sample.get("stats_summary", {}).items():
        if isinstance(stat, dict):
            if "mean" in stat:
                stats_lines.append(f"  {col}: moyenne={stat.get('mean')}, min={stat.get('min')}, max={stat.get('max')}")
            elif "top_values" in stat:
                top = stat.get("top_values", [])[:3]
                stats_lines.append(f"  {col}: {stat.get('unique_count')} valeurs uniques, top: {', '.join(str(v) for v in top)}")

    user_msg = f"""Dataset :
- Lignes : {sample.get('row_count', 0):,}
- Colonnes : {sample.get('columns', [])}
- Numériques : {sample.get('numeric_columns', [])}
- Catégorielles : {sample.get('categorical_columns', [])}
- Dates : {sample.get('date_columns', [])}

Statistiques :
{chr(10).join(stats_lines) or '  (non disponibles)'}

Échantillon (10 lignes) :
{json.dumps(sample.get('sample_rows', [])[:10], indent=2, ensure_ascii=False, default=str)}

Réponds UNIQUEMENT avec le JSON."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": SUGGEST_SYSTEM_PROMPT}, {"role": "user", "content": user_msg}],
        temperature=0.2, max_tokens=2000,
    )
    result = _parse_json(response.choices[0].message.content.strip())
    result.setdefault("insights", [])
    result.setdefault("summary", "")
    result["insights"] = result["insights"][:6]
    for i, ins in enumerate(result["insights"]):
        ins.setdefault("id", f"insight_{i+1}")
        ins.setdefault("title", f"Insight {i+1}")
        ins.setdefault("description", "")
        ins.setdefault("type", "comparison")
        ins.setdefault("feasibility", "high")
        ins.setdefault("required_columns", [])
    return result


# ============================================================================
# APPEL 2 — RAPPORT FINAL (après /confirm)
#
# Pandas a déjà calculé les vraies données avec mean/sum/count.
# OpenAI reçoit les données et des instructions précises sur quelle
# agrégation utiliser → plus jamais de "somme des moyennes générales".
# ============================================================================

FINAL_REPORT_SYSTEM_PROMPT = """Tu es un expert en data visualization et business intelligence.
Tu reçois des données RÉELLES déjà agrégées par Pandas et les insights sélectionnés.
Produis un rapport JSON complet et IMPACTANT pour un dashboard interactif Chart.js.

══════════════════════════════════════════════════════
RÈGLE CRITIQUE N°1 — AGRÉGATION CORRECTE
══════════════════════════════════════════════════════
Chaque bloc de données "groupby" dans le message contient :
  - "mean_values"  : MOYENNE par catégorie
  - "sum_values"   : SOMME par catégorie
  - "count_values" : NOMBRE d'éléments par catégorie
  - "recommended_aggregation" : "mean" ou "sum"
  - "instruction"  : explication précise de ce qu'il faut utiliser

TU DOIS RESPECTER CES INSTRUCTIONS. Exemples :
  ✅ Moyenne générale par filière → utilise "mean_values" (la somme des moyennes n'a aucun sens)
  ✅ Frais de scolarité par filière → utilise "sum_values" (le total est pertinent)
  ✅ Nombre d'étudiants par statut → utilise "count_values"
  ❌ JAMAIS additionner des notes/scores/moyennes/taux

══════════════════════════════════════════════════════
RÈGLE CRITIQUE N°2 — GRAPHIQUES IMPACTANTS
══════════════════════════════════════════════════════
Chaque graphique doit :
  1. Répondre à UNE question métier précise
  2. Utiliser le BON type de graphique :
     - bar    : comparer des catégories (ex: performance par filière)
     - line   : montrer une évolution temporelle
     - doughnut : répartition en % (max 6 catégories)
     - pie    : parts de marché simples (max 5 catégories)
     - scatter : corrélation entre 2 variables numériques
  3. Avoir une "description" analytique qui explique CE QUE LE GRAPHIQUE RÉVÈLE
  4. Avoir un "insight" qui est LA CONCLUSION ACTIONNABLE en 1 phrase

══════════════════════════════════════════════════════
RÈGLE CRITIQUE N°3 — KPIs PERTINENTS
══════════════════════════════════════════════════════
Pour chaque KPI :
  - Score/note/moyenne/taux/age → affiche la MOYENNE (mean)
  - Montant/quantité/volume → affiche le TOTAL (sum)
  - Respecte le champ "recommended_kpi_value" dans les données

══════════════════════════════════════════════════════
RÈGLE CRITIQUE N°4 — DEVISE MONÉTAIRE
══════════════════════════════════════════════════════
- Si les données contiennent déjà une devise identifiable (€, $, £, etc.) → utilise cette devise
- Si AUCUNE devise n'est présente dans les données → utilise le FRANC CFA (FCFA) automatiquement
- Le FCFA est la devise par défaut pour toutes les valeurs monétaires sans devise explicite
- Exemples : "29 330 000 FCFA" / "146 650 FCFA" / "37 190 €" (si € détecté dans les données)

══════════════════════════════════════════════════════
RÈGLES GÉNÉRALES
══════════════════════════════════════════════════════
- Réponds UNIQUEMENT avec un JSON valide — zéro texte, zéro markdown, zéro backtick
- N'invente AUCUNE valeur — utilise EXACTEMENT les données fournies
- 3 à 5 KPIs maximum
- 2 à 4 graphiques variés (pas deux fois le même type si possible)
- Les insights doivent avoir une "interpretation" actionnable concrète

STRUCTURE OBLIGATOIRE :
{
  "summary": {
    "title": "Titre accrocheur du rapport",
    "description": "4-6 phrases : contexte, découvertes principales, tendances, recommandations concrètes avec chiffres.",
    "key_takeaways": [
      "Point clé 1 avec chiffre précis tiré des données",
      "Point clé 2 — observation importante",
      "Point clé 3 — recommandation actionnable"
    ]
  },
  "kpis": [
    {
      "id": "kpi_1",
      "label": "Nom du KPI",
      "value": "Valeur formatée lisible (ex: 12.35 ou 146 650 ou 73%)",
      "raw_value": 12.35,
      "unit": "FCFA | % | unité | (VIDE si l'unité est déjà incluse dans value — voir règle devise ci-dessous)",
      "trend": "up|down|stable|null",
      "trend_value": "texte optionnel (ex: +5% vs mois précédent) ou null",
      "description": "Ce que mesure ce KPI et pourquoi c'est important",
      "icon": "euro|percent|users|chart|trending-up|trending-down|target|clock|package|star"
    }
  ],
  "charts": [
    {
      "id": "chart_1",
      "type": "bar|line|pie|doughnut|scatter",
      "title": "Titre descriptif qui dit CE QUE MONTRE le graphique",
      "description": "2-3 phrases : ce que révèle ce graphique, quelle tendance ou écart est notable, et pourquoi c'est important.",
      "insight": "LA conclusion actionnable en 1 phrase courte (ex: 'Les étudiants de Gestion ont la meilleure moyenne à 13.2/20')",
      "data": {
        "labels": ["Label1", "Label2", "Label3"],
        "datasets": [
          {
            "label": "Nom de la série",
            "data": [12.5, 11.8, 13.1]
          }
        ]
      },
      "options": {
        "x_label": "Axe X",
        "y_label": "Axe Y",
        "stacked": false,
        "show_legend": true
      }
    }
  ],
  "insights": [
    {
      "id": "insight_1",
      "title": "Titre de l'insight",
      "description": "Description détaillée avec les chiffres clés.",
      "value": "Résultat chiffré principal (ex: '13.2/20 en Gestion vs 11.5/20 en Marketing')",
      "type": "trend|comparison|total|anomaly|distribution",
      "interpretation": "Que faire concrètement de cette information ? (recommandation actionnable)",
      "data": {}
    }
  ]
}

TYPES DE GRAPHIQUES — rappel :
- scatter : "data" ne contient PAS "labels". Contient uniquement : {"datasets": [{"label": "...", "data": [{"x": 1.2, "y": 3.4}, ...]}]}
- pie/doughnut : "data" = {"labels": [...], "datasets": [{"data": [...nombres...]}]} — PAS de "label" dans le dataset"""


def generate_final_report(aggregated_data: dict, selected_insights: list, metadata: dict) -> dict:
    client = get_openai_client()

    numeric_cols    = metadata.get("numeric_columns", [])
    categorical_cols = metadata.get("categorical_columns", [])
    text_cols       = metadata.get("text_columns", [])
    date_cols       = metadata.get("date_columns", [])
    statistics      = metadata.get("statistics", {})

    # Résumé stats global pour contexte
    stats_lines = []
    all_cols = numeric_cols + [c for c in text_cols if statistics.get(c, {}).get("type") in ["text", "categorical"]]
    for col in all_cols[:12]:
        stat = statistics.get(col, {})
        if stat.get("type") in ["integer", "float"]:
            agg = _best_aggregation_label(col)
            stats_lines.append(
                f"  {col} [{agg}]: moyenne={stat.get('mean')}, min={stat.get('min')}, "
                f"max={stat.get('max')}, total={stat.get('sum')}"
            )
        elif stat.get("type") in ["text", "categorical"]:
            top = stat.get("top_values", [])[:3]
            top_str = ", ".join(f"{v['value']}={v['pct']}%" for v in top)
            stats_lines.append(f"  {col}: {stat.get('unique_count')} valeurs → {top_str}")

    insights_str = "\n".join([
        f"  [{ins.get('type','?')}] {ins.get('title','')}: {ins.get('description','')}"
        for ins in selected_insights
    ])

    user_msg = f"""DATASET :
- {metadata.get('row_count', 0):,} lignes | {metadata.get('column_count', 0)} colonnes
- Colonnes numériques : {numeric_cols}
- Colonnes texte/catégorielles : {text_cols}
- Colonnes dates : {date_cols}

STATISTIQUES GLOBALES :
{chr(10).join(stats_lines) or '  (non disponibles)'}

INSIGHTS SÉLECTIONNÉS PAR L'UTILISATEUR :
{insights_str or '  (aucun)'}

══════════════════════════════════════════════════════
DONNÉES RÉELLES AGRÉGÉES (utilise ces valeurs EXACTES)
RESPECTE LES "instruction" et "recommended_aggregation" dans chaque bloc
══════════════════════════════════════════════════════
{json.dumps(aggregated_data, ensure_ascii=False, indent=2, default=str)}

Génère le rapport JSON complet. Réponds UNIQUEMENT avec le JSON."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": FINAL_REPORT_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg}
        ],
        temperature=0.15,
        max_tokens=4000,
    )

    result = _parse_json(response.choices[0].message.content.strip())
    result.setdefault("summary", {"title": "Rapport d'analyse", "description": "", "key_takeaways": []})
    result.setdefault("kpis", [])
    result.setdefault("charts", [])
    result.setdefault("insights", [])

    for i, chart in enumerate(result["charts"]):
        chart.setdefault("id", f"chart_{i+1}")
        chart.setdefault("options", {})
        chart["options"].setdefault("show_legend", True)
        chart["options"].setdefault("stacked", False)

    for i, kpi in enumerate(result["kpis"]):
        kpi.setdefault("id", f"kpi_{i+1}")
        kpi.setdefault("trend", None)
        kpi.setdefault("trend_value", None)
        kpi.setdefault("icon", "chart")

    for i, ins in enumerate(result["insights"]):
        ins.setdefault("id", f"insight_{i+1}")
        ins.setdefault("interpretation", "")
        ins.setdefault("data", {})

    return result


def _best_aggregation_label(col: str) -> str:
    c = col.lower()
    if any(k in c for k in ["moyenne", "mean", "note", "score", "taux", "rate", "age", "gpa"]):
        return "MOYENNE"
    if any(k in c for k in ["frais", "montant", "prix", "cost", "revenue", "vente", "total", "budget"]):
        return "SOMME"
    return "MOYENNE"


# ============================================================================
# VALIDATION INSIGHT PERSONNALISÉ
# ============================================================================

VALIDATE_INSIGHT_PROMPT = """Tu es un expert en analyse de données.
Reçois une demande d'insight + colonnes disponibles.
Retourne UNIQUEMENT un JSON valide (sans markdown) :
{
  "valid": true | false,
  "reformulated_title": "Titre court (si valid=true)",
  "reformulated_description": "Description reformulée (si valid=true)",
  "required_columns": ["col1", "col2"],
  "type": "trend|comparison|total|anomaly|distribution",
  "feasibility": "high|medium|low",
  "reason": "Explication si non réalisable (null si valid=true)"
}
Interprète avec bienveillance même si la demande est vague."""


def validate_custom_insight(description: str, columns: list, column_types: dict) -> dict:
    client = get_openai_client()
    user_msg = f"""Demande : "{description}"

Colonnes :
{json.dumps({col: column_types.get(col, 'text') for col in columns}, ensure_ascii=False, indent=2)}"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": VALIDATE_INSIGHT_PROMPT}, {"role": "user", "content": user_msg}],
        temperature=0.2, max_tokens=500,
    )
    result = _parse_json(response.choices[0].message.content.strip())
    result.setdefault("valid", False)
    result.setdefault("reformulated_title", None)
    result.setdefault("reformulated_description", None)
    result.setdefault("required_columns", [])
    result.setdefault("type", "comparison")
    result.setdefault("feasibility", "medium")
    result.setdefault("reason", None)
    result["error"] = None
    return result


# ============================================================================
# QUESTION IA SUR UNE ANALYSE
# ============================================================================

def ask_question_on_analysis(question: str, analysis_results: dict, metadata: dict) -> str:
    client = get_openai_client()
    summary = analysis_results.get("summary", {})
    kpis    = analysis_results.get("kpis", [])
    insights = analysis_results.get("insights", [])

    kpi_str = "\n".join([f"  - {k['label']}: {k['value']}" for k in kpis[:5]])
    ins_str = "\n".join([f"  - {i['title']}: {i.get('value','')}" for i in insights[:5]])

    context = f"""Tu es un assistant expert en data analysis.
Rapport : {summary.get('title', 'Analyse')}
Résumé : {summary.get('description', '')}
KPIs : {kpi_str or '(aucun)'}
Insights : {ins_str or '(aucun)'}
Réponds en français, de manière claire et concise."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": context}, {"role": "user", "content": question}],
        temperature=0.3, max_tokens=800,
    )
    return response.choices[0].message.content.strip()


# ============================================================================
# HELPER JSON
# ============================================================================

def _parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end])
        raise ValueError(f"Impossible de parser la réponse OpenAI : {raw[:300]}")