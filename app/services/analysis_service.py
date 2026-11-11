"""
ANALYSIS SERVICE

Pipeline après /confirm :
1. Charge le dataset complet depuis le JSON
2. Pandas calcule les vraies agrégations — mean/sum choisis intelligemment selon le type de métrique
3. Envoie tout à OpenAI → reçoit le JSON final (graphiques + KPIs + résumé)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional
from app.services.excel_service import load_from_json


# ============================================================================
# PIPELINE PRINCIPAL
# ============================================================================

def run_analysis_pipeline(
    dataset_id: str,
    sample: dict,
    metadata: dict,
    ai_instructions: dict,
    selected_insights: list,
) -> dict:
    from app.services.openai_service import generate_final_report

    dataset_stem = Path(dataset_id).stem if dataset_id else dataset_id
    json_path = Path("data/uploads") / f"{dataset_stem}.json"
    print(f"📂 Pipeline: {dataset_id!r} → {json_path}")
    print(f"📂 exists: {json_path.exists()} | cwd: {Path.cwd()}")
    df, _ = load_from_json(str(json_path))
    print(f"✅ Dataset chargé: {len(df)} lignes")

    aggregated_data = compute_aggregations(df, metadata, selected_insights)
    report = generate_final_report(aggregated_data, selected_insights, metadata)

    return {
        "dataset_id": dataset_stem,
        "metadata": metadata,
        "summary": report.get("summary", {}),
        "kpis": report.get("kpis", []),
        "charts": report.get("charts", []),
        "insights": report.get("insights", []),
        "ai_instructions": ai_instructions,
    }


# ============================================================================
# RÈGLE FONDAMENTALE D'AGRÉGATION
#
# score/note/moyenne/taux/age → MEAN  (la somme n'a aucun sens métier)
# montant/quantité/volume     → SUM   (le total est la métrique naturelle)
# Par défaut → MEAN (plus souvent pertinent)
# On envoie TOUJOURS mean + sum + count pour que l'IA puisse choisir
# ============================================================================

def _best_aggregation(col: str) -> str:
    c = col.lower()
    if any(k in c for k in ["moyenne", "mean", "avg", "note", "score", "taux", "rate",
                              "ratio", "pct", "percent", "age", "satisfaction",
                              "evaluation", "grade", "gpa", "index", "indice"]):
        return "mean"
    if any(k in c for k in ["frais", "montant", "amount", "prix", "price", "cost", "cout",
                              "revenue", "revenu", "vente", "sale", "chiffre", "total",
                              "quantite", "quantity", "stock", "volume", "budget",
                              "salaire", "wage", "salary", "fee", "charge"]):
        return "sum"
    return "mean"


def _groupby_smart(df: pd.DataFrame, cat_col: str, num_col: str, max_items: int = 15) -> dict:
    """
    Calcule mean + sum + count pour chaque catégorie.
    Précise quelle agrégation utiliser dans le graphique.
    """
    agg_rec = _best_aggregation(num_col)
    grp = df.groupby(cat_col)[num_col].agg(["mean", "sum", "count"])
    sort_by = "mean" if agg_rec == "mean" else "sum"
    grp = grp.sort_values(sort_by, ascending=False).head(max_items)

    return {
        "labels": [str(k) for k in grp.index.tolist()],
        "mean_values": [round(float(v), 2) for v in grp["mean"].tolist()],
        "sum_values":  [round(float(v), 2) for v in grp["sum"].tolist()],
        "count_values": [int(v) for v in grp["count"].tolist()],
        "recommended_aggregation": agg_rec,
        "x_col": cat_col,
        "y_col": num_col,
        "instruction": (
            f"IMPORTANT: Pour '{num_col}' groupé par '{cat_col}', utilise les "
            f"{'mean_values (MOYENNE par catégorie)' if agg_rec == 'mean' else 'sum_values (TOTAL par catégorie)'}. "
            f"{'La somme des moyennes générales ne veut rien dire — affiche la moyenne.' if agg_rec == 'mean' else 'Le total est la métrique la plus parlante ici.'}"
        ),
    }


# ============================================================================
# CALCUL DES AGRÉGATIONS
# ============================================================================

def compute_aggregations(
    df: pd.DataFrame,
    metadata: dict,
    selected_insights: list,
) -> dict:
    numeric_cols = metadata.get("numeric_columns", [])
    date_cols    = metadata.get("date_columns", [])
    statistics   = metadata.get("statistics", {})
    text_cols    = metadata.get("text_columns", [])

    # Colonnes catégorielles effectives (text avec 2–25 valeurs uniques, pas ID)
    id_keywords = ["student_id", "_id", "id", "index", "nom", "name", "prenom"]
    effective_cats = [
        c for c in text_cols
        if c in df.columns
        and 2 <= df[c].nunique() <= 25
        and not any(k in c.lower() for k in id_keywords)
    ]

    aggregated = {}

    # ── 1. KPIs GLOBAUX ──────────────────────────────────────────────────────
    global_kpis = {}
    for col in numeric_cols[:8]:
        if col not in df.columns:
            continue
        s = df[col].dropna()
        if len(s) == 0:
            continue
        agg = _best_aggregation(col)
        global_kpis[col] = {
            "mean":   round(float(s.mean()), 2),
            "median": round(float(s.median()), 2),
            "sum":    round(float(s.sum()), 2),
            "min":    round(float(s.min()), 2),
            "max":    round(float(s.max()), 2),
            "std":    round(float(s.std()), 2),
            "count":  int(s.count()),
            "recommended_kpi_value": agg,
            "instruction": f"Affiche la {'MOYENNE (mean)' if agg == 'mean' else 'SOMME (sum)'} pour ce KPI",
        }
    aggregated["global_kpis"] = global_kpis

    # ── 2. GROUPBY : toutes combinaisons catégorie × numérique ───────────────
    groupby_results = {}
    for cat_col in effective_cats[:5]:
        for num_col in numeric_cols[:4]:
            if num_col not in df.columns:
                continue
            key = f"{num_col}_par_{cat_col}"
            groupby_results[key] = _groupby_smart(df, cat_col, num_col)
    aggregated["groupby"] = groupby_results

    # ── 3. DISTRIBUTIONS CATÉGORIELLES ───────────────────────────────────────
    distributions = {}
    for col in effective_cats[:6]:
        vc = df[col].value_counts()
        total = int(vc.sum())
        distributions[col] = {
            "labels":      [str(k) for k in vc.index.tolist()],
            "counts":      [int(v) for v in vc.values.tolist()],
            "percentages": [round(v / total * 100, 1) for v in vc.values.tolist()],
            "total":       total,
            "instruction": "Utilise les 'percentages' pour pie/doughnut, les 'counts' pour bar",
        }
    aggregated["distributions"] = distributions

    # ── 4. CORRÉLATIONS NUMÉRIQUES ────────────────────────────────────────────
    correlations = {}
    num_in_df = [c for c in numeric_cols if c in df.columns]
    for i in range(min(len(num_in_df), 4)):
        for j in range(i + 1, min(len(num_in_df), 4)):
            c1, c2 = num_in_df[i], num_in_df[j]
            try:
                corr = round(float(df[c1].corr(df[c2])), 3)
                strength = "forte" if abs(corr) > 0.7 else ("modérée" if abs(corr) > 0.4 else "faible")
                direction = "positive" if corr > 0 else "négative"
                pts = df[[c1, c2]].dropna().head(200)
                correlations[f"{c1}_vs_{c2}"] = {
                    "col1": c1, "col2": c2,
                    "correlation": corr,
                    "interpretation": f"Corrélation {strength} {direction} ({corr})",
                    "scatter_points": [
                        {"x": round(float(r[c1]), 2), "y": round(float(r[c2]), 2)}
                        for _, r in pts.iterrows()
                    ],
                    "instruction": "Utilise 'scatter_points' pour un graphique scatter",
                }
            except Exception:
                pass
    aggregated["correlations"] = correlations

    # ── 5. ÉVOLUTION TEMPORELLE ──────────────────────────────────────────────
    temporal = {}
    if date_cols:
        date_col = date_cols[0]
        if date_col in df.columns:
            for num_col in numeric_cols[:3]:
                if num_col not in df.columns:
                    continue
                try:
                    tmp = df[[date_col, num_col]].copy()
                    tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
                    tmp = tmp.dropna(subset=[date_col])
                    n_days = (tmp[date_col].max() - tmp[date_col].min()).days
                    freq  = "ME" if n_days > 365 else ("W" if n_days > 90 else "D")
                    label = "mensuelle" if freq == "ME" else ("hebdomadaire" if freq == "W" else "journalière")
                    agg = _best_aggregation(num_col)
                    grp = tmp.set_index(date_col).resample(freq)[num_col]
                    ts = (grp.mean() if agg == "mean" else grp.sum()).dropna().head(50)
                    temporal[f"{num_col}_{label}"] = {
                        "labels": [str(d.date()) for d in ts.index],
                        "values": [round(float(v), 2) for v in ts.values],
                        "aggregation_used": agg,
                        "granularity": label,
                        "x_col": date_col, "y_col": num_col,
                    }
                except Exception as e:
                    print(f"⚠️ Temporel {num_col}: {e}")
    aggregated["temporal"] = temporal

    # ── 6. DONNÉES PAR INSIGHT SÉLECTIONNÉ ───────────────────────────────────
    insights_data = {}
    for insight in selected_insights:
        iid   = insight.get("id", "")
        cols  = insight.get("required_columns", [])
        itype = insight.get("type", "comparison")
        valid = [c for c in cols if c in df.columns]
        if not valid:
            continue
        try:
            result = _compute_insight_data(df, itype, valid)
            if result:
                insights_data[iid] = {
                    "title": insight.get("title", ""),
                    "type": itype, "columns": valid, **result,
                }
        except Exception as e:
            print(f"⚠️ Insight {iid}: {e}")
    aggregated["insights_data"] = insights_data

    return aggregated


def _compute_insight_data(df: pd.DataFrame, itype: str, cols: list) -> Optional[dict]:
    num_cols_here = [c for c in cols if c in df.select_dtypes(include="number").columns]
    cat_cols_here = [c for c in cols if c not in num_cols_here and df[c].nunique() <= 25]

    if itype in ["comparison", "trend", "total"] and cat_cols_here and num_cols_here:
        return _groupby_smart(df, cat_cols_here[0], num_cols_here[0])

    elif itype in ["comparison", "trend"] and len(num_cols_here) >= 2:
        c1, c2 = num_cols_here[0], num_cols_here[1]
        corr = round(float(df[c1].corr(df[c2])), 3)
        pts = df[[c1, c2]].dropna().head(200)
        return {
            "correlation": corr,
            "scatter_points": [{"x": round(float(r[c1]), 2), "y": round(float(r[c2]), 2)} for _, r in pts.iterrows()],
            "recommended_chart": "scatter",
        }

    elif itype == "distribution":
        col = cols[0]
        if col in df.select_dtypes(include="number").columns:
            counts, edges = np.histogram(df[col].dropna(), bins=10)
            return {
                "labels": [f"{edges[i]:.1f}–{edges[i+1]:.1f}" for i in range(len(counts))],
                "counts": counts.tolist(),
                "recommended_chart": "bar",
            }
        vc = df[col].value_counts()
        total = int(vc.sum())
        return {
            "labels": [str(k) for k in vc.index.tolist()],
            "counts": [int(v) for v in vc.values.tolist()],
            "percentages": [round(v / total * 100, 1) for v in vc.values.tolist()],
            "recommended_chart": "doughnut" if vc.nunique() <= 6 else "bar",
            "instruction": "Utilise les 'percentages' pour pie/doughnut",
        }

    elif itype == "anomaly" and num_cols_here:
        col = num_cols_here[0]
        mean, std = df[col].mean(), df[col].std()
        threshold = mean + 2 * std
        anomalies = df[df[col] > threshold]
        return {
            "mean": round(float(mean), 2),
            "std": round(float(std), 2),
            "threshold_high": round(float(threshold), 2),
            "anomaly_count": int(len(anomalies)),
            "anomaly_pct": round(len(anomalies) / len(df) * 100, 1),
        }

    return None