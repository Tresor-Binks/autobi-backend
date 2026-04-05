"""
EXCEL SERVICE

Responsabilités :
- Lire les fichiers Excel/CSV
- Valider la structure du fichier
- Nettoyer les données (valeurs manquantes)
- Analyser la structure du dataset (colonnes, types, stats complètes)
- Convertir en JSON enrichi et sauvegarder sur le serveur
- Créer un échantillon pour OpenAI
"""

import pandas as pd
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


# ============================================================================
# CONFIGURATION
# ============================================================================

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_ROWS = 30
MAX_MISSING_PCT = 0.05  # 5% maximum de cellules vides


# ============================================================================
# VALIDATION DE LA STRUCTURE DU FICHIER
# ============================================================================

def validate_file_structure(file_path: str) -> dict:
    """
    Valide qu'un fichier Excel répond aux critères d'analyse :
    - Une seule feuille
    - Un seul tableau continu
    - Maximum 5% de cellules vides
    """
    ext = Path(file_path).suffix.lower()
    errors = []
    warnings = []
    sheet_count = 1
    row_count = 0
    column_count = 0
    missing_pct = 0.0

    try:
        if ext in [".xlsx", ".xls"]:
            xl = pd.ExcelFile(file_path)
            sheet_count = len(xl.sheet_names)

            if sheet_count > 1:
                errors.append(
                    f"Le fichier contient {sheet_count} feuilles. "
                    f"Une seule feuille est autorisée. "
                    f"Feuilles détectées : {', '.join(xl.sheet_names)}"
                )

            df = pd.read_excel(file_path, sheet_name=0)

        elif ext == ".csv":
            sheet_count = 1
            df = pd.read_csv(file_path, sep=None, engine="python")
        else:
            return {
                "valid": False,
                "errors": [f"Format non supporté : {ext}. Utilisez .xlsx, .xls ou .csv"],
                "warnings": [],
                "sheet_count": 0,
                "row_count": 0,
                "column_count": 0,
                "missing_pct": 0.0
            }

        row_count = len(df)
        column_count = len(df.columns)

        if row_count == 0:
            errors.append("Le fichier est vide ou ne contient pas de données.")
            return {"valid": False, "errors": errors, "warnings": warnings,
                    "sheet_count": sheet_count, "row_count": 0, "column_count": 0, "missing_pct": 0.0}

        if column_count == 0:
            errors.append("Aucune colonne détectée dans le fichier.")
            return {"valid": False, "errors": errors, "warnings": warnings,
                    "sheet_count": sheet_count, "row_count": 0, "column_count": 0, "missing_pct": 0.0}

        # Détection multi-tableaux via lignes vides
        empty_rows = df.isna().all(axis=1)
        if empty_rows.any():
            first_empty = empty_rows.idxmax()
            rows_after_empty = df.iloc[first_empty:].dropna(how="all")
            if len(rows_after_empty) > 1:
                errors.append(
                    "Le fichier semble contenir plusieurs tableaux séparés par des lignes vides. "
                    "Assurez-vous que le fichier ne contient qu'un seul tableau continu."
                )
            else:
                warnings.append("Des lignes vides ont été détectées à la fin du fichier.")
                df = df.dropna(how="all")
                row_count = len(df)

        # Détection multi-tableaux via colonnes vides
        empty_cols = df.isna().all(axis=0)
        if empty_cols.any():
            found_empty = False
            non_empty_after = False
            for col in df.columns:
                if df[col].isna().all():
                    found_empty = True
                elif found_empty:
                    non_empty_after = True
                    break
            if non_empty_after:
                errors.append(
                    "Le fichier semble contenir plusieurs tableaux côte à côte. "
                    "Assurez-vous que le fichier ne contient qu'un seul tableau."
                )
            else:
                warnings.append("Des colonnes entièrement vides ont été détectées et seront ignorées.")
                df = df.dropna(axis=1, how="all")
                column_count = len(df.columns)

        # Pourcentage de cellules vides
        total_cells = row_count * column_count
        if total_cells > 0:
            missing_cells = int(df.isna().sum().sum())
            missing_pct = round(missing_cells / total_cells, 4)
            if missing_pct > MAX_MISSING_PCT:
                errors.append(
                    f"Le fichier contient {round(missing_pct * 100, 1)}% de cellules vides "
                    f"(maximum autorisé : {int(MAX_MISSING_PCT * 100)}%). "
                    f"Soit {missing_cells} cellules vides sur {total_cells}."
                )
            elif missing_pct > 0.02:
                warnings.append(f"{round(missing_pct * 100, 1)}% de cellules vides détectées.")

        # Colonnes sans nom
        unnamed_cols = [col for col in df.columns if str(col).startswith("Unnamed:")]
        if unnamed_cols:
            warnings.append(f"{len(unnamed_cols)} colonne(s) sans nom détectée(s).")

        # Colonnes dupliquées
        duplicate_cols = df.columns[df.columns.duplicated()].tolist()
        if duplicate_cols:
            errors.append(f"Noms de colonnes en double : {', '.join(str(c) for c in duplicate_cols)}.")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "sheet_count": sheet_count,
            "row_count": row_count,
            "column_count": column_count,
            "missing_pct": missing_pct
        }

    except Exception as e:
        return {
            "valid": False,
            "errors": [f"Impossible de lire le fichier : {str(e)}"],
            "warnings": [],
            "sheet_count": 0,
            "row_count": 0,
            "column_count": 0,
            "missing_pct": 0.0
        }


# ============================================================================
# LECTURE DU FICHIER
# ============================================================================

def read_file(file_path: str) -> pd.DataFrame:
    """Lit un fichier Excel ou CSV et retourne un DataFrame brut."""
    ext = Path(file_path).suffix.lower()

    if ext in [".xlsx", ".xls"]:
        df = pd.read_excel(file_path, sheet_name=0)
    elif ext == ".csv":
        df = pd.read_csv(file_path, sep=None, engine="python")
    else:
        raise ValueError(f"Format non supporté : {ext}.")

    # Suppression des lignes/colonnes entièrement vides
    df = df.dropna(how="all").dropna(axis=1, how="all")

    return df


# ============================================================================
# NETTOYAGE DES DONNÉES
# ============================================================================

def clean_dataframe(df: pd.DataFrame) -> tuple:
    """
    Nettoie le DataFrame :
    - Colonnes numériques : remplace les valeurs manquantes par la moyenne
    - Colonnes texte/date/catégorielles : supprime les lignes avec valeurs manquantes

    Returns:
        (df_cleaned, cleaning_report)
    """
    df = df.copy()
    cleaning_report = {
        "numeric_filled": {},
        "text_rows_dropped": {},
        "rows_before": len(df),
        "rows_after": 0,
    }

    # 1. Colonnes numériques → remplacement par la moyenne
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    for col in numeric_cols:
        missing_count = int(df[col].isna().sum())
        if missing_count > 0:
            mean_val = df[col].mean()
            df[col] = df[col].fillna(mean_val)
            cleaning_report["numeric_filled"][col] = {
                "count": missing_count,
                "fill_value": round(float(mean_val), 4)
            }

    # 2. Colonnes non numériques → suppression des lignes manquantes
    non_numeric_cols = df.select_dtypes(exclude=["number"]).columns.tolist()
    for col in non_numeric_cols:
        missing_count = int(df[col].isna().sum())
        if missing_count > 0:
            df = df.dropna(subset=[col])
            cleaning_report["text_rows_dropped"][col] = missing_count

    cleaning_report["rows_after"] = len(df)
    df = df.reset_index(drop=True)

    return df, cleaning_report


# ============================================================================
# DÉTECTION DES TYPES DE COLONNES
# ============================================================================

def detect_column_types(df: pd.DataFrame) -> dict:
    """
    Détecte les types sémantiques de chaque colonne.

    Types possibles : integer, float, text, date, boolean, categorical
    """
    col_types = {}

    for col in df.columns:
        dtype = df[col].dtype
        series = df[col].dropna()

        info = {
            "pandas_type": str(dtype),
            "semantic_type": "text",
            "is_numeric": False,
            "is_date": False,
            "is_categorical": False,
        }

        if pd.api.types.is_bool_dtype(dtype):
            info["semantic_type"] = "boolean"

        elif pd.api.types.is_integer_dtype(dtype):
            info["semantic_type"] = "integer"
            info["is_numeric"] = True

        elif pd.api.types.is_float_dtype(dtype):
            info["semantic_type"] = "float"
            info["is_numeric"] = True

        elif pd.api.types.is_datetime64_any_dtype(dtype):
            info["semantic_type"] = "date"
            info["is_date"] = True

        elif pd.api.types.is_object_dtype(dtype):
            # Tentative de détection de date
            try:
                pd.to_datetime(series, infer_datetime_format=True)
                info["semantic_type"] = "date"
                info["is_date"] = True
            except Exception:
                # Détection catégorielle
                unique_ratio = series.nunique() / len(series) if len(series) > 0 else 0
                if unique_ratio < 0.2 and series.nunique() <= 50:
                    info["semantic_type"] = "categorical"
                    info["is_categorical"] = True
                else:
                    info["semantic_type"] = "text"

        col_types[col] = info

    return col_types


# ============================================================================
# STATISTIQUES COMPLÈTES
# ============================================================================

def compute_statistics(df: pd.DataFrame, col_types: dict) -> dict:
    """
    Calcule les statistiques complètes pour chaque colonne.

    Numériques  : count, mean, median, mode, std, min, max, q25, q75, iqr, sum
    Dates       : count, min_date, max_date, range_days
    Catégorielles/Texte : count, unique_count, mode, top_values (valeur + fréquence + %)
    """
    stats = {}

    for col in df.columns:
        col_info = col_types.get(col, {})
        series = df[col].dropna()

        if col_info.get("is_numeric"):
            mode_vals = series.mode()
            mode = float(mode_vals.iloc[0]) if len(mode_vals) > 0 else None

            stats[col] = {
                "type": col_info["semantic_type"],
                "count": int(series.count()),
                "null_count": int(df[col].isna().sum()),
                "mean": round(float(series.mean()), 4) if len(series) > 0 else None,
                "median": round(float(series.median()), 4) if len(series) > 0 else None,
                "mode": round(mode, 4) if mode is not None else None,
                "std": round(float(series.std()), 4) if len(series) > 1 else None,
                "min": round(float(series.min()), 4) if len(series) > 0 else None,
                "max": round(float(series.max()), 4) if len(series) > 0 else None,
                "sum": round(float(series.sum()), 4) if len(series) > 0 else None,
                "q25": round(float(series.quantile(0.25)), 4) if len(series) > 0 else None,
                "q75": round(float(series.quantile(0.75)), 4) if len(series) > 0 else None,
                "iqr": round(float(series.quantile(0.75) - series.quantile(0.25)), 4) if len(series) > 0 else None,
            }

        elif col_info.get("is_date"):
            try:
                date_series = pd.to_datetime(series)
                min_date = date_series.min()
                max_date = date_series.max()
                stats[col] = {
                    "type": "date",
                    "count": int(series.count()),
                    "null_count": int(df[col].isna().sum()),
                    "min_date": min_date.isoformat() if pd.notna(min_date) else None,
                    "max_date": max_date.isoformat() if pd.notna(max_date) else None,
                    "range_days": int((max_date - min_date).days) if pd.notna(min_date) and pd.notna(max_date) else None,
                }
            except Exception:
                stats[col] = {
                    "type": "date",
                    "count": int(series.count()),
                    "null_count": int(df[col].isna().sum()),
                }

        else:
            # Catégorielle ou texte
            value_counts = series.value_counts()
            top_values = [
                {
                    "value": str(val),
                    "count": int(cnt),
                    "pct": round(cnt / len(series) * 100, 1)
                }
                for val, cnt in value_counts.head(10).items()
            ]
            mode_vals = series.mode()
            mode = str(mode_vals.iloc[0]) if len(mode_vals) > 0 else None

            stats[col] = {
                "type": col_info.get("semantic_type", "text"),
                "count": int(series.count()),
                "null_count": int(df[col].isna().sum()),
                "unique_count": int(series.nunique()),
                "mode": mode,
                "top_values": top_values,
            }

    return stats


# ============================================================================
# ANALYSE GLOBALE DU DATASET
# ============================================================================

def analyze_dataframe(df: pd.DataFrame) -> dict:
    """
    Analyse complète du DataFrame après nettoyage.
    """
    col_types = detect_column_types(df)
    stats = compute_statistics(df, col_types)

    numeric_cols = [col for col, info in col_types.items() if info.get("is_numeric")]
    date_cols = [col for col, info in col_types.items() if info.get("is_date")]
    categorical_cols = [col for col, info in col_types.items() if info.get("is_categorical")]
    text_cols = [
        col for col, info in col_types.items()
        if not info.get("is_numeric") and not info.get("is_date") and not info.get("is_categorical")
    ]

    return {
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": df.columns.tolist(),
        "column_types": col_types,
        "numeric_columns": numeric_cols,
        "date_columns": date_cols,
        "categorical_columns": categorical_cols,
        "text_columns": text_cols,
        "statistics": stats,
        "missing_values": int(df.isna().sum().sum()),
    }


# ============================================================================
# CRÉATION DE L'ÉCHANTILLON POUR OPENAI
# ============================================================================

def create_sample(df: pd.DataFrame, metadata: dict) -> dict:
    """Crée un échantillon compact du dataset pour OpenAI."""
    sample_df = df.head(SAMPLE_ROWS)
    sample_rows = sample_df.where(pd.notnull(sample_df), None).to_dict(orient="records")

    cleaned_rows = []
    for row in sample_rows:
        cleaned_row = {}
        for k, v in row.items():
            if isinstance(v, (pd.Timestamp, datetime)):
                cleaned_row[k] = v.isoformat()
            elif isinstance(v, float) and v != v:
                cleaned_row[k] = None
            elif hasattr(v, 'item'):
                cleaned_row[k] = v.item()
            else:
                cleaned_row[k] = v
        cleaned_rows.append(cleaned_row)

    # Résumé allégé des stats pour OpenAI
    stats_summary = {}
    for col, stat in metadata["statistics"].items():
        if stat["type"] in ["integer", "float"]:
            stats_summary[col] = {
                "mean": stat.get("mean"),
                "min": stat.get("min"),
                "max": stat.get("max"),
                "median": stat.get("median"),
            }
        elif stat["type"] == "categorical":
            stats_summary[col] = {
                "unique_count": stat.get("unique_count"),
                "top_values": [v["value"] for v in stat.get("top_values", [])[:5]],
            }

    return {
        "columns": metadata["columns"],
        "column_types": {col: info["semantic_type"] for col, info in metadata["column_types"].items()},
        "numeric_columns": metadata["numeric_columns"],
        "categorical_columns": metadata["categorical_columns"],
        "date_columns": metadata["date_columns"],
        "row_count": metadata["row_count"],
        "sample_rows": cleaned_rows,
        "stats_summary": stats_summary,
    }


# ============================================================================
# SAUVEGARDE JSON ENRICHI
# ============================================================================

def save_as_json(df: pd.DataFrame, metadata: dict, cleaning_report: dict, json_path: Path) -> str:
    """
    Sauvegarde le dataset nettoyé + métadonnées + statistiques dans un JSON structuré.

    Structure :
    {
        "_meta": {
            row_count, column_count, columns,
            column_types, numeric_columns, date_columns,
            categorical_columns, text_columns,
            statistics: { col: { mean, median, mode, std, min, max, q25, q75, iqr, sum, ... } },
            cleaning_report: { numeric_filled, text_rows_dropped, rows_before, rows_after },
            created_at
        },
        "data": [ { col: val, ... }, ... ]
    }
    """
    def serialize(obj):
        if isinstance(obj, (pd.Timestamp, datetime)):
            return obj.isoformat()
        if isinstance(obj, float) and obj != obj:
            return None
        if hasattr(obj, 'item'):
            return obj.item()
        return str(obj)

    # Nettoyage des records
    records = df.where(pd.notnull(df), None).to_dict(orient="records")
    cleaned_records = []
    for row in records:
        cleaned_row = {}
        for k, v in row.items():
            if isinstance(v, (pd.Timestamp, datetime)):
                cleaned_row[k] = v.isoformat()
            elif isinstance(v, float) and v != v:
                cleaned_row[k] = None
            elif hasattr(v, 'item'):
                cleaned_row[k] = v.item()
            else:
                cleaned_row[k] = v
        cleaned_records.append(cleaned_row)

    output = {
        "_meta": {
            "row_count": metadata["row_count"],
            "column_count": metadata["column_count"],
            "columns": metadata["columns"],
            "column_types": metadata["column_types"],
            "numeric_columns": metadata["numeric_columns"],
            "date_columns": metadata["date_columns"],
            "categorical_columns": metadata["categorical_columns"],
            "text_columns": metadata["text_columns"],
            "statistics": metadata["statistics"],
            "cleaning_report": cleaning_report,
            "created_at": datetime.utcnow().isoformat() + "Z",
        },
        "data": cleaned_records
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, default=serialize, ensure_ascii=False, indent=2)

    return str(json_path)


# ============================================================================
# CHARGEMENT DU JSON
# ============================================================================

def load_from_json(json_path: str) -> tuple:
    """
    Charge un dataset depuis le JSON enrichi.

    Returns:
        (DataFrame, metadata)
    """
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset introuvable : {json_path}")

    with open(path, "r", encoding="utf-8") as f:
        content = json.load(f)

    # Support ancien format (liste simple) et nouveau format enrichi
    if isinstance(content, list):
        return pd.DataFrame(content), {}

    metadata = content.get("_meta", {})
    data = content.get("data", [])
    return pd.DataFrame(data), metadata


# ============================================================================
# PIPELINE COMPLET : FICHIER → JSON ENRICHI
# ============================================================================

def process_uploaded_file(file_path: str, json_path: Path) -> dict:
    """
    Pipeline complet :
    1. Lecture du fichier
    2. Nettoyage (valeurs manquantes)
    3. Analyse et statistiques complètes
    4. Sauvegarde JSON enrichi

    Returns:
        { json_path, metadata, sample, cleaning_report }
    """
    df = read_file(file_path)
    df_clean, cleaning_report = clean_dataframe(df)
    metadata = analyze_dataframe(df_clean)
    save_as_json(df_clean, metadata, cleaning_report, json_path)
    sample = create_sample(df_clean, metadata)

    return {
        "json_path": str(json_path),
        "metadata": metadata,
        "sample": sample,
        "cleaning_report": cleaning_report,
    }