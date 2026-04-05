"""
ANALYSIS SERVICE

Responsabilités :
- Orchestrer le pipeline complet d'analyse
- Exécuter les analyses Pandas selon les instructions d'OpenAI
- Générer les données de graphiques
- Produire les insights et explications
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional
from app.services.excel_service import load_from_json
from app.services.openai_service import get_analysis_instructions


# ============================================================================
# PIPELINE PRINCIPAL
# ============================================================================

def run_analysis_pipeline(dataset_id: str, sample: dict, metadata: dict) -> dict:
    """
    Pipeline complet d'analyse.
    """
    # Instructions OpenAI
    instructions = get_analysis_instructions(sample)

    # Chargement du dataset complet
    json_path = Path("data/uploads") / f"{dataset_id}.json"
    df, _ = load_from_json(str(json_path))

    # Exécution des analyses
    charts_data = execute_charts(df, instructions.get("charts", []), metadata)
    insights_data = execute_insights(df, instructions.get("analyses", []), metadata)
    explanations = generate_explanations(insights_data, metadata)

    return {
        "dataset_id": dataset_id,
        "metadata": metadata,
        "charts": charts_data,
        "insights": insights_data,
        "explanations": explanations,
        "ai_instructions": instructions,
    }


# ============================================================================
# FALLBACK COHÉRENT — GRAPHIQUES ET INSIGHTS INTELLIGENTS
# ============================================================================

def generate_fallback_analysis(df: pd.DataFrame, metadata: dict) -> dict:
    """
    Génère une analyse cohérente sans OpenAI.
    
    Stratégie :
    - 1 graphique par type de colonne disponible (max 4)
    - Insights statistiques clairs et non redondants
    - Priorité aux colonnes les plus pertinentes
    """
    numeric_cols = metadata.get("numeric_columns", [])
    categorical_cols = metadata.get("categorical_columns", [])
    date_cols = metadata.get("date_columns", [])
    text_cols = metadata.get("text_columns", [])
    stats = metadata.get("statistics", {})

    charts = []
    insights = []

    # ----------------------------------------------------------------
    # GRAPHIQUES — un par combinaison utile, sans redondance
    # ----------------------------------------------------------------

    # 1. Évolution temporelle si colonne date + numérique
    if date_cols and numeric_cols:
        date_col = date_cols[0]
        num_col = _pick_best_numeric(numeric_cols, stats)
        charts.append(_build_line_chart(df, date_col, num_col))

    # 2. Comparaison catégorielle si catégorielle + numérique
    cat_col = _pick_best_categorical(categorical_cols + text_cols, df)
    if cat_col and numeric_cols:
        num_col = _pick_best_numeric(numeric_cols, stats)
        charts.append(_build_bar_chart(df, cat_col, num_col))

    # 3. Distribution d'une colonne numérique clé
    if numeric_cols:
        num_col = _pick_best_numeric(numeric_cols, stats)
        dist_chart = _build_distribution_chart(df, num_col)
        if dist_chart:
            charts.append(dist_chart)

    # 4. Corrélation entre deux numériques différentes (si > 1)
    if len(numeric_cols) >= 2:
        col1 = _pick_best_numeric(numeric_cols, stats)
        remaining = [c for c in numeric_cols if c != col1]
        col2 = _pick_best_numeric(remaining, stats)
        if col1 != col2:
            charts.append(_build_scatter_chart(df, col1, col2))

    # ----------------------------------------------------------------
    # INSIGHTS — statistiques claires et non redondantes
    # ----------------------------------------------------------------

    seen_cols = set()

    # Insight 1 : résumé statistique de la colonne numérique principale
    if numeric_cols:
        col = _pick_best_numeric(numeric_cols, stats)
        seen_cols.add(col)
        s = stats.get(col, {})
        if s:
            insights.append({
                "type": "stats",
                "column": col,
                "title": f"Résumé — {col}",
                "value": (
                    f"Moyenne : {_fmt(s.get('mean'))} | "
                    f"Médiane : {_fmt(s.get('median'))} | "
                    f"Min : {_fmt(s.get('min'))} | "
                    f"Max : {_fmt(s.get('max'))}"
                ),
                "data": {
                    "mean": s.get("mean"),
                    "median": s.get("median"),
                    "min": s.get("min"),
                    "max": s.get("max"),
                    "sum": s.get("sum"),
                }
            })

    # Insight 2 : top catégorie si colonne catégorielle disponible
    if cat_col and cat_col not in seen_cols:
        seen_cols.add(cat_col)
        s = stats.get(cat_col, {})
        top = s.get("top_values", [])
        if top:
            top_val = top[0]
            insights.append({
                "type": "top_values",
                "column": cat_col,
                "title": f"Répartition — {cat_col}",
                "value": (
                    f"Valeur dominante : \"{top_val.get('value')}\" "
                    f"({top_val.get('pct', 0):.1f}% des entrées). "
                    f"{s.get('unique_count', 0)} valeurs distinctes."
                ),
                "data": {v["value"]: v["count"] for v in top[:6]}
            })

    # Insight 3 : deuxième colonne numérique si disponible et différente
    if len(numeric_cols) >= 2:
        remaining = [c for c in numeric_cols if c not in seen_cols]
        if remaining:
            col = _pick_best_numeric(remaining, stats)
            seen_cols.add(col)
            s = stats.get(col, {})
            if s:
                insights.append({
                    "type": "stats",
                    "column": col,
                    "title": f"Résumé — {col}",
                    "value": (
                        f"Moyenne : {_fmt(s.get('mean'))} | "
                        f"Min : {_fmt(s.get('min'))} | "
                        f"Max : {_fmt(s.get('max'))}"
                    ),
                    "data": {
                        "mean": s.get("mean"),
                        "min": s.get("min"),
                        "max": s.get("max"),
                    }
                })

    return {"charts": charts, "insights": insights}


# ============================================================================
# HELPERS — SÉLECTION INTELLIGENTE DES COLONNES
# ============================================================================

def _pick_best_numeric(cols: list, stats: dict) -> str:
    """
    Choisit la colonne numérique la plus pertinente :
    - Évite les colonnes ID (faible variance)
    - Préfère les colonnes avec forte variance relative
    """
    if not cols:
        return ""
    
    best = cols[0]
    best_score = -1
    
    for col in cols:
        # Ignore les colonnes qui ressemblent à des IDs
        col_lower = col.lower()
        if any(x in col_lower for x in ["id", "index", "num", "numero"]):
            continue
        
        s = stats.get(col, {})
        mean = s.get("mean", 0) or 0
        std = s.get("std", 0) or 0
        
        # Coefficient de variation
        cv = (std / mean) if mean != 0 else 0
        
        if cv > best_score:
            best_score = cv
            best = col
    
    return best


def _pick_best_categorical(cols: list, df: pd.DataFrame) -> Optional[str]:
    """
    Choisit la colonne catégorielle la plus pertinente :
    - Entre 2 et 20 valeurs uniques
    - Évite les colonnes avec trop de valeurs uniques (ex: noms propres)
    """
    for col in cols:
        if col not in df.columns:
            continue
        unique_count = df[col].nunique()
        if 2 <= unique_count <= 20:
            return col
    return None


def _fmt(val) -> str:
    """Formate un nombre pour l'affichage."""
    if val is None:
        return "—"
    try:
        v = float(val)
        if v == int(v):
            return f"{int(v):,}".replace(",", " ")
        return f"{v:,.2f}".replace(",", " ")
    except Exception:
        return str(val)


# ============================================================================
# BUILDERS DE GRAPHIQUES
# ============================================================================

def _build_line_chart(df: pd.DataFrame, x_col: str, y_col: str) -> dict:
    grouped = df.groupby(x_col)[y_col].mean().reset_index().head(50)
    return {
        "type": "line",
        "title": f"Évolution de {y_col}",
        "x_label": x_col,
        "y_label": y_col,
        "description": f"Évolution moyenne de {y_col} au fil du temps.",
        "data": {
            "labels": [str(v) for v in grouped[x_col].tolist()],
            "datasets": [{"label": y_col, "data": [round(float(v), 2) for v in grouped[y_col].tolist()]}]
        }
    }


def _build_bar_chart(df: pd.DataFrame, x_col: str, y_col: str) -> dict:
    grouped = df.groupby(x_col)[y_col].mean().sort_values(ascending=False).head(15).reset_index()
    return {
        "type": "bar",
        "title": f"{y_col} par {x_col}",
        "x_label": x_col,
        "y_label": y_col,
        "description": f"Comparaison de la moyenne de {y_col} selon {x_col}.",
        "data": {
            "labels": [str(v) for v in grouped[x_col].tolist()],
            "datasets": [{"label": f"Moyenne {y_col}", "data": [round(float(v), 2) for v in grouped[y_col].tolist()]}]
        }
    }


def _build_distribution_chart(df: pd.DataFrame, col: str) -> Optional[dict]:
    """Histogramme de distribution en 10 tranches."""
    series = df[col].dropna()
    if len(series) < 5:
        return None
    
    counts, bin_edges = np.histogram(series, bins=10)
    labels = [f"{bin_edges[i]:.1f}–{bin_edges[i+1]:.1f}" for i in range(len(counts))]
    
    return {
        "type": "bar",
        "title": f"Distribution de {col}",
        "x_label": col,
        "y_label": "Nombre d'entrées",
        "description": f"Répartition des valeurs de {col} en 10 intervalles.",
        "data": {
            "labels": labels,
            "datasets": [{"label": "Fréquence", "data": counts.tolist()}]
        }
    }


def _build_scatter_chart(df: pd.DataFrame, x_col: str, y_col: str) -> dict:
    sample = df[[x_col, y_col]].dropna().head(200)
    corr = round(float(sample[x_col].corr(sample[y_col])), 2) if len(sample) > 1 else 0
    return {
        "type": "scatter",
        "title": f"Relation entre {x_col} et {y_col}",
        "x_label": x_col,
        "y_label": y_col,
        "description": f"Nuage de points entre {x_col} et {y_col}. Corrélation : {corr}.",
        "data": {
            "datasets": [{
                "label": f"{x_col} vs {y_col}",
                "data": [{"x": float(r[x_col]), "y": float(r[y_col])} for _, r in sample.iterrows()]
            }]
        }
    }


# ============================================================================
# EXÉCUTION DES GRAPHIQUES (instructions OpenAI)
# ============================================================================

def execute_charts(df: pd.DataFrame, chart_instructions: list, metadata: dict) -> list:
    """
    Si OpenAI a donné des instructions → les exécute.
    Sinon → fallback cohérent.
    """
    if not chart_instructions:
        fallback = generate_fallback_analysis(df, metadata)
        return fallback["charts"]

    charts = []
    for instruction in chart_instructions:
        try:
            chart = build_chart(df, instruction)
            if chart:
                charts.append(chart)
        except Exception as e:
            pass  # On ignore silencieusement les graphiques invalides

    # Si OpenAI n'a rien produit de valide → fallback
    if not charts:
        fallback = generate_fallback_analysis(df, metadata)
        return fallback["charts"]

    return charts


def build_chart(df: pd.DataFrame, instruction: dict) -> Optional[dict]:
    chart_type = instruction.get("type", "bar")
    x_col = instruction.get("x")
    y_col = instruction.get("y")
    aggregation = instruction.get("aggregation", "sum")
    title = instruction.get("title", f"{y_col} par {x_col}")
    description = instruction.get("description", "")

    if x_col not in df.columns or (y_col and y_col not in df.columns):
        return None

    if chart_type == "pie":
        return build_pie_chart(df, x_col, y_col, aggregation, title, description)
    elif chart_type in ["line", "bar"]:
        return build_xy_chart(df, x_col, y_col, aggregation, chart_type, title, description)
    elif chart_type == "scatter":
        return build_scatter_chart_ai(df, x_col, y_col, title, description)
    else:
        return build_xy_chart(df, x_col, y_col, aggregation, "bar", title, description)


def build_xy_chart(df, x_col, y_col, aggregation, chart_type, title, description=""):
    if aggregation == "count":
        grouped = df.groupby(x_col)[y_col].count().reset_index()
    elif aggregation == "mean":
        grouped = df.groupby(x_col)[y_col].mean().reset_index()
    else:
        grouped = df.groupby(x_col)[y_col].sum().reset_index()

    grouped = grouped.head(50)
    return {
        "type": chart_type, "title": title,
        "x_label": x_col, "y_label": y_col,
        "description": description,
        "data": {
            "labels": [str(v) for v in grouped[x_col].tolist()],
            "datasets": [{"label": y_col, "data": [round(float(v), 2) if v is not None else 0 for v in grouped[y_col].tolist()]}]
        }
    }


def build_pie_chart(df, x_col, y_col, aggregation, title, description=""):
    if y_col and y_col in df.columns:
        grouped = df.groupby(x_col)[y_col].sum().reset_index()
        values = grouped[y_col].tolist()
        labels = grouped[x_col].tolist()
    else:
        counts = df[x_col].value_counts().head(10)
        labels = counts.index.tolist()
        values = counts.values.tolist()
    return {
        "type": "pie", "title": title, "description": description,
        "data": {
            "labels": [str(l) for l in labels],
            "datasets": [{"data": [round(float(v), 2) for v in values]}]
        }
    }


def build_scatter_chart_ai(df, x_col, y_col, title, description=""):
    sample = df[[x_col, y_col]].dropna().head(200)
    return {
        "type": "scatter", "title": title,
        "x_label": x_col, "y_label": y_col,
        "description": description,
        "data": {
            "datasets": [{
                "label": f"{x_col} vs {y_col}",
                "data": [{"x": float(r[x_col]), "y": float(r[y_col])} for _, r in sample.iterrows()]
            }]
        }
    }


# ============================================================================
# EXÉCUTION DES INSIGHTS
# ============================================================================

def execute_insights(df: pd.DataFrame, analyses: list, metadata: dict) -> list:
    """
    Si OpenAI a donné des analyses → les exécute.
    Sinon → fallback cohérent.
    """
    if not analyses:
        fallback = generate_fallback_analysis(df, metadata)
        return fallback["insights"]

    insights = []

    for analysis in analyses:
        try:
            result = execute_single_analysis(df, analysis)
            if result:
                insights.append(result)
        except Exception:
            pass

    # Complète avec des stats de base si peu de résultats
    if len(insights) < 2:
        fallback = generate_fallback_analysis(df, metadata)
        for fi in fallback["insights"]:
            if not any(i.get("column") == fi.get("column") and i.get("type") == fi.get("type") for i in insights):
                insights.append(fi)

    return insights


def execute_single_analysis(df: pd.DataFrame, analysis: dict) -> Optional[dict]:
    analysis_type = analysis.get("type")
    columns = analysis.get("columns", [])
    description = analysis.get("description", "")
    valid_cols = [c for c in columns if c in df.columns]
    if not valid_cols:
        return None

    if analysis_type == "trend" and len(valid_cols) >= 2:
        x_col, y_col = valid_cols[0], valid_cols[1]
        grouped = df.groupby(x_col)[y_col].sum()
        if len(grouped) >= 2:
            first, last = float(grouped.iloc[0]), float(grouped.iloc[-1])
            change = round(((last - first) / first) * 100, 1) if first != 0 else 0
            direction = "augmenté" if change > 0 else "diminué"
            return {
                "type": "trend", "title": description,
                "value": f"La valeur a {direction} de {abs(change)}% entre le début et la fin de la période.",
                "data": {"change_percent": change}
            }

    elif analysis_type == "groupby" and len(valid_cols) >= 2:
        x_col, y_col = valid_cols[0], valid_cols[1]
        grouped = df.groupby(x_col)[y_col].sum().sort_values(ascending=False).head(5)
        return {
            "type": "groupby", "title": description,
            "value": f"Top 5 par {x_col}",
            "data": {str(k): round(float(v), 2) for k, v in grouped.items()}
        }

    elif analysis_type == "correlation" and len(valid_cols) >= 2:
        col1, col2 = valid_cols[0], valid_cols[1]
        num_df = df.select_dtypes(include="number")
        if col1 in num_df.columns and col2 in num_df.columns:
            corr = round(float(df[col1].corr(df[col2])), 3)
            strength = "forte" if abs(corr) > 0.7 else "modérée" if abs(corr) > 0.4 else "faible"
            direction = "positive" if corr > 0 else "négative"
            return {
                "type": "correlation", "title": description,
                "value": f"Corrélation {strength} {direction} entre {col1} et {col2} : {corr}",
                "data": {"correlation": corr}
            }

    return None


# ============================================================================
# EXPLICATIONS TEXTUELLES
# ============================================================================

def generate_explanations(insights: list, metadata: dict) -> list:
    explanations = []

    row_count = metadata.get("row_count", 0)
    col_count = metadata.get("column_count", 0)
    num_cols = metadata.get("numeric_columns", [])
    cat_cols = metadata.get("categorical_columns", [])

    explanations.append(
        f"Le dataset contient {row_count:,} lignes et {col_count} colonnes "
        f"({len(num_cols)} numériques, {len(cat_cols)} catégorielles).".replace(",", " ")
    )

    missing = metadata.get("missing_values", 0)
    if missing > 0:
        total = row_count * col_count
        pct = round((missing / total) * 100, 1) if total > 0 else 0
        explanations.append(f"{missing} valeurs manquantes détectées ({pct}% du dataset) — remplacées par la moyenne.")

    for insight in insights:
        if insight.get("type") == "trend" and "value" in insight:
            explanations.append(insight["value"])

    return explanations