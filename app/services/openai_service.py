"""
OPENAI SERVICE

1. get_analysis_instructions(sample)  — pendant l'upload → suggestions d'insights métier
2. generate_final_report(...)         — après /confirm → rapport JSON complet

Principe : Python calcule et vérifie les chiffres (faits), l'IA les interprète
en langage métier et propose des actions concrètes. L'IA n'invente aucun chiffre.
"""

import json
import os
import re
import unicodedata
from openai import OpenAI

MODEL = "gpt-4o-mini"


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
# OUTILS : rôles des colonnes (déduits des noms, accents ignorés)
# ============================================================================

def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return s.lower()


def _tokens(col) -> list:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(col))  # IdClient → Id Client
    return [t for t in re.split(r"[^a-z0-9]+", _norm(spaced)) if t]


# (rôle, mots exacts, fragments longs)
_ROLE_RULES = [
    ("identifiant", {"id", "ref", "code", "matricule", "sku", "numero", "num", "uuid"}, ["identifiant", "reference"]),
    ("taux", {"taux", "rate", "ratio", "pct", "percent"}, ["pourcentage", "percent", "satisfaction"]),
    ("note", {"note", "score", "gpa", "grade", "moyenne", "index", "indice", "evaluation", "age"}, []),
    ("montant", {"montant", "amount", "ca", "vente", "sale", "revenu", "revenue", "total", "cout", "cost",
                 "budget", "salaire", "salary", "wage", "frais", "fee", "marge", "margin", "benefice",
                 "profit", "depense", "charge", "paiement", "recette"},
     ["chiffre", "revenu", "montant", "salaire"]),
    ("prix_unitaire", {"prix", "price", "tarif", "pu"}, ["unitaire"]),
    ("quantite", {"quantite", "quantity", "qte", "qty", "volume", "stock", "nombre", "nb", "unite", "unit"}, ["quantit"]),
    ("duree", {"duree", "delai", "delay", "duration", "jours"}, []),
]

_ROLE_LABELS = {
    "identifiant": "identifiant — ne jamais additionner ni moyenner, ne pas analyser",
    "taux": "taux / pourcentage — MOYENNE",
    "note": "note / score / âge — MOYENNE",
    "montant": "montant — SOMME (total)",
    "prix_unitaire": "prix unitaire probable — MOYENNE, jamais additionné",
    "quantite": "quantité / volume — SOMME (total)",
    "duree": "durée / délai — MOYENNE",
}


def _role(col):
    toks = _tokens(col)
    n = _norm(col)
    for role, words, stems in _ROLE_RULES:
        if any(t in words or (t.endswith("s") and t[:-1] in words) for t in toks):
            return role
        if any(s in n for s in stems):
            return role
    return None


def _metric_for(col, recommended="mean") -> str:
    """'sum' ou 'mean' selon la NATURE de la colonne (prime sur la recommandation par mots-clés)."""
    r = _role(col)
    if r in ("montant", "quantite"):
        return "sum"
    if r in ("taux", "note", "prix_unitaire", "duree"):
        return "mean"
    return recommended if recommended in ("mean", "sum") else "mean"


def _column_hints(columns, numeric_cols) -> str:
    lines = []
    for c in columns:
        r = _role(c)
        if r and (r == "identifiant" or c in numeric_cols):
            lines.append(f"  {c} → {_ROLE_LABELS[r]}")
    return "\n".join(lines)


def _best_aggregation_label(col: str) -> str:
    r = _role(col)
    if r == "identifiant":
        return "IDENTIFIANT — ignorer"
    return "SOMME" if _metric_for(col) == "sum" else "MOYENNE"


# ============================================================================
# OUTILS : formatage et faits calculés par Python
# ============================================================================

def _fmt(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if x != x:
        return "n/a"
    if abs(x) >= 1000:
        return f"{x:,.0f}".replace(",", " ")
    if abs(x) >= 100:
        return f"{x:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _pct(part, total) -> str:
    if not total:
        return "n/a"
    return f"{part / total * 100:.1f}".replace(".", ",") + " %"


def _slope_rel(vals) -> float:
    """Variation totale approximée par la régression linéaire, relative à l'ordre de grandeur de la série."""
    n = len(vals)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2
    my = sum(vals) / n
    den = sum((x - mx) ** 2 for x in range(n))
    if den == 0:
        return 0.0
    slope = sum((x - mx) * (v - my) for x, v in zip(range(n), vals)) / den
    scale = (sum(abs(v) for v in vals) / n) or 1.0
    return slope * (n - 1) / scale


def _build_facts_digest(aggregated_data: dict, metadata: dict) -> str:
    """Faits chiffrés, calculés en Python (parts, écarts, tendances, fiabilité). L'IA les cite, ne les recalcule pas."""
    lines = []
    meta = metadata or {}
    agg = aggregated_data or {}
    gk = agg.get("global_kpis") or {}

    # — Contexte du fichier —
    n_rows = meta.get("row_count") or 0
    if n_rows:
        lines.append(f"- Volume : {n_rows} lignes." + (
            " PETIT ÉCHANTILLON (< 30 lignes) : conclusions à confirmer sur plus de données." if n_rows < 30 else ""))
    for col, st in (meta.get("statistics") or {}).items():
        if isinstance(st, dict) and st.get("type") == "date" and st.get("min_date"):
            lines.append(f"- Période couverte ({col}) : du {str(st['min_date'])[:10]} au {str(st.get('max_date'))[:10]}"
                         f" ({st.get('range_days')} jours).")

    # — Indicateurs globaux —
    for col, k in gk.items():
        try:
            r = _role(col)
            if r == "identifiant":
                continue
            total_txt = f"total {_fmt(k['sum'])}, " if _metric_for(col) == "sum" else ""  # un total de prix/taux/notes n'a pas de sens
            part = (f"- {col} [{_best_aggregation_label(col)}] : {total_txt}moyenne {_fmt(k['mean'])}, "
                    f"médiane {_fmt(k['median'])}, min {_fmt(k['min'])}, max {_fmt(k['max'])} ({k['count']} lignes)")
            notes = []
            if k.get("mean") and k.get("std") is not None and abs(k["std"]) > abs(k["mean"]):
                notes.append("dispersion très forte (écart-type > moyenne) : la moyenne est peu représentative, "
                             "préférer la médiane")
            if k.get("mean") and k["mean"] > 0 and k.get("max") and k["max"] > 5 * k["mean"]:
                notes.append(f"le maximum vaut {k['max'] / k['mean']:.1f} × la moyenne : valeur(s) extrême(s) à vérifier")
            lines.append(part + (" ; " + " ; ".join(notes) if notes else "") + ".")
        except Exception:
            pass

    # — Comparaisons par catégorie —
    gb_lines, qty, price = [], {}, {}
    for key, g in (agg.get("groupby") or {}).items():
        try:
            xcol, ycol = g.get("x_col"), g.get("y_col")
            labels = g.get("labels") or []
            role = _role(ycol)
            if role == "identifiant" or len(labels) < 2:
                continue
            sums, means = g.get("sum_values") or [], g.get("mean_values") or []
            counts = g.get("count_values") or []
            if role == "quantite" and len(sums) == len(labels):
                qty.setdefault(xcol, {}).update(dict(zip(labels, sums)))
            if role == "prix_unitaire" and len(means) == len(labels):
                price.setdefault(xcol, {}).update(dict(zip(labels, means)))
            metric = _metric_for(ycol, g.get("recommended_aggregation", "mean"))
            vals = sums if metric == "sum" else means
            if len(vals) != len(labels):
                continue
            order = sorted(range(len(vals)), key=lambda i: vals[i], reverse=True)
            i1, il = order[0], order[-1]
            name = "total" if metric == "sum" else "moyenne"
            txt = f"- {ycol} par {xcol} ({name}) : n°1 {labels[i1]} = {_fmt(vals[i1])}"
            if metric == "sum" and sum(vals):
                txt += f" ({_pct(vals[i1], sum(vals))} du total)"
            if len(order) > 2:
                i2 = order[1]
                txt += f" ; n°2 {labels[i2]} = {_fmt(vals[i2])}"
            txt += f" ; dernier {labels[il]} = {_fmt(vals[il])}"
            if vals[il] and vals[il] > 0 and vals[i1] > 0:
                txt += f" ; écart n°1/dernier ×{vals[i1] / vals[il]:.1f}"
            if counts and len(counts) == len(labels):
                ci = max(range(len(counts)), key=lambda i: counts[i])
                txt += f" ; le plus d'opérations : {labels[ci]} ({counts[ci]})"
            if len(labels) >= 15:
                txt += f" (parmi les {len(labels)} premières modalités)"
            gb_lines.append(txt + ".")
        except Exception:
            pass
    lines.extend(gb_lines[:14])

    # — Chiffre d'affaires estimé (quantité × prix moyen) si le fichier n'a pas de colonne montant —
    if not any(_role(c) == "montant" for c in gk):
        for xcol in qty:
            try:
                common = [l for l in qty[xcol] if l in price.get(xcol, {})]
                if len(common) < 2:
                    continue
                est = {l: qty[xcol][l] * price[xcol][l] for l in common}
                tot = sum(est.values())
                top = sorted(est.items(), key=lambda kv: kv[1], reverse=True)[:3]
                lines.append(
                    f"- CA ESTIMÉ par {xcol} (ESTIMATION = quantité totale × prix moyen du groupe ; le fichier n'a pas de "
                    f"colonne montant) : total {_fmt(tot)} ; " +
                    " ; ".join(f"{l} = {_fmt(v)} ({_pct(v, tot)})" for l, v in top) + ".")
            except Exception:
                pass

    # — Répartitions —
    for col, d in (agg.get("distributions") or {}).items():
        try:
            labels, counts, pcts = d.get("labels") or [], d.get("counts") or [], d.get("percentages") or []
            if len(labels) < 2 or _role(col) == "identifiant":
                continue
            top = ", ".join(f"{labels[i]} {str(pcts[i]).replace('.', ',')} %" for i in range(min(4, len(labels))))
            txt = f"- Répartition {col} ({d.get('total')} lignes, {len(labels)} modalités) : {top} ; " \
                  f"plus petite : {labels[-1]} {str(pcts[-1]).replace('.', ',')} %"
            if pcts[0] >= 50:
                txt += f" ; CONCENTRATION : {labels[0]} pèse à lui seul {str(pcts[0]).replace('.', ',')} %"
            lines.append(txt + ".")
        except Exception:
            pass

    # — Relations entre colonnes —
    for key, c in (agg.get("correlations") or {}).items():
        try:
            r = c.get("correlation")
            if r is None or r != r:
                continue
            c1, c2 = c.get("col1"), c.get("col2")
            if "identifiant" in (_role(c1), _role(c2)):
                continue
            n = len(c.get("scatter_points") or [])
            txt = f"- Relation {c1} ↔ {c2} : r = {str(r).replace('.', ',')} sur {n} lignes"
            if n < 10:
                txt += " → FIABILITÉ TRÈS FAIBLE (moins de 10 points), ne pas en tirer de conclusion"
            elif n < 30:
                txt += " → fiabilité limitée (moins de 30 points)"
            if abs(r) < 0.3:
                txt += " ; pas de lien linéaire exploitable"
            lines.append(txt + ". Une corrélation ne prouve pas une causalité.")
        except Exception:
            pass

    # — Évolutions dans le temps —
    for key, t in (agg.get("temporal") or {}).items():
        try:
            labels, vals = t.get("labels") or [], t.get("values") or []
            n = len(vals)
            ycol = t.get("y_col")
            if n < 2 or len(labels) != n or _role(ycol) == "identifiant":
                continue
            used = t.get("aggregation_used", "mean")
            if used == "sum" and _metric_for(ycol) == "mean":
                # ex. prix unitaire additionné par période : valeur sans signification métier
                lines.append(f"- Évolution {ycol} : NON EXPLOITABLE (la série additionne des valeurs de nature « moyenne » "
                             f"— prix, taux ou notes) : ne pas l'utiliser pour un graphique ni pour une tendance.")
                continue
            first, last = vals[0], vals[-1]
            pi, ti = max(range(n), key=lambda i: vals[i]), min(range(n), key=lambda i: vals[i])
            chg = f"{(last - first) / abs(first) * 100:+.1f}".replace(".", ",") + " %" if first else "n/a"
            what = "total par période" if used == "sum" else "MOYENNE par opération et par période (pas un total)"
            txt = (f"- Évolution {ycol} ({what}, {t.get('granularity')}, {n} points, {labels[0]} → {labels[-1]}) : "
                   f"{_fmt(first)} → {_fmt(last)} ({chg}) ; pic {labels[pi]} = {_fmt(vals[pi])} ; "
                   f"creux {labels[ti]} = {_fmt(vals[ti])}")
            if n < 4:
                txt += " ; TROP PEU DE POINTS pour parler de tendance"
            else:
                s = _slope_rel(vals)
                if abs(s) > 0.10 and (last - first) * s < 0:
                    direction = "IRRÉGULIÈRE (pente et écart début/fin de sens contraire : pas de tendance nette)"
                else:
                    direction = "HAUSSE" if s > 0.10 else "BAISSE" if s < -0.10 else "STABLE"
                txt += " ; tendance : " + direction
                if vals[-2]:
                    txt += f" ; dernier point vs précédent : {(vals[-1] - vals[-2]) / abs(vals[-2]) * 100:+.1f}".replace(".", ",") + " %"
            lines.append(txt + ".")
        except Exception:
            pass

    # — Anomalies —
    for iid, d in (agg.get("insights_data") or {}).items():
        try:
            if "anomaly_count" in d:
                cols = ", ".join(d.get("columns") or [])
                txt = (f"- Anomalies {cols} : {d['anomaly_count']} valeur(s) au-dessus du seuil {_fmt(d['threshold_high'])} "
                       f"({str(d['anomaly_pct']).replace('.', ',')} % des lignes ; moyenne {_fmt(d['mean'])})")
                if d["anomaly_count"] == 0:
                    txt += " — aucune valeur extrême détectée par cette méthode (peu sensible sur petit échantillon)"
                lines.append(txt + ".")
        except Exception:
            pass

    return "\n".join(lines) or "(aucun fait calculable)"


def _even_sample(points: list, n: int) -> list:
    if len(points) <= n:
        return points
    step = len(points) / n
    return [points[int(i * step)] for i in range(n)]


def _trim_for_prompt(obj):
    """Allège le JSON envoyé : nuage de points limité à 60, consignes génériques retirées (les faits les remplacent)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "instruction":
                continue
            out[k] = _even_sample(v, 60) if k == "scatter_points" and isinstance(v, list) else _trim_for_prompt(v)
        return out
    if isinstance(obj, list):
        return [_trim_for_prompt(x) for x in obj]
    return obj


def _chat_json(client, system: str, user: str, temperature: float, max_tokens: int) -> dict:
    """Appel JSON robuste : mode json_object + une relance si la réponse est invalide ou tronquée."""
    last_err = None
    for attempt in range(2):
        content = user if attempt == 0 else (
            user + "\n\nLa réponse précédente était invalide ou tronquée : sois plus concis "
                   "(textes courts) et renvoie un JSON complet et valide.")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        kwargs = dict(model=MODEL, messages=messages, temperature=temperature, max_tokens=max_tokens)
        try:
            resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except Exception as e:
            if e.__class__.__name__ == "BadRequestError" and "response_format" in str(e):
                resp = client.chat.completions.create(**kwargs)
            else:
                raise
        try:
            return _parse_json(resp.choices[0].message.content.strip())
        except ValueError as e:
            last_err = e
    raise ValueError(f"Réponse IA invalide après 2 essais : {last_err}")


# ============================================================================
# APPEL 1 — SUGGESTIONS D'INSIGHTS (pendant l'upload)
# ============================================================================

SUGGEST_SYSTEM_PROMPT = """Tu es un analyste BI senior. Tu prépares une analyse pour le dirigeant d'une PME qui n'a pas de data analyst.
Tu reçois la structure d'un fichier (colonnes, types, statistiques, 10 lignes). Tu proposes les analyses qui l'aideront à DÉCIDER.

ÉTAPE 1 — Déduis le contexte métier (ventes, stock, RH, scolarité, finance, logistique, santé...) à partir des noms de colonnes et des valeurs, puis adopte le vocabulaire de ce métier.

ÉTAPE 2 — Propose 4 à 6 insights. Chacun répond à UNE question de gestion concrète que le dirigeant se pose
(ex. : « Quels produits portent mon chiffre d'affaires ? », « Quelle ville sous-performe ? », « Quelle filière décroche ? »),
jamais à une description générique.
- Varie les angles : classement/comparaison, total, concentration/répartition ; "trend" UNIQUEMENT s'il existe une colonne date ; "anomaly" UNIQUEMENT s'il existe une colonne numérique pertinente.
- Utilise UNIQUEMENT des colonnes qui existent, avec leur nom EXACT. required_columns : 1 à 3 colonnes (une dimension + une mesure, ou une date + une mesure).
- N'utilise JAMAIS un identifiant (id, référence, code, matricule) comme mesure.
- Ne propose pas ce que les données ne permettent pas (marge sans coût, évolution sans date...).
- feasibility : "high" = colonnes présentes et exploitables ; "medium" = possible mais limité (peu de lignes ou de modalités, valeurs manquantes) ; "low" = partiel.
- title : 8 mots max, formulé comme un constat à vérifier (pas une généralité).
- description (1-2 phrases) : la question métier + ce qui sera calculé + la décision que cela éclaire.
- Pas de doublon : deux insights n'ont pas le même couple de colonnes pour la même question.

SUMMARY (3 à 5 phrases) :
1) ce que contient le fichier, avec des chiffres (nombre de lignes, entités principales) ;
2) 1 à 2 faits marquants visibles dans les statistiques fournies (valeur, écart) ;
3) les limites des données qui pèsent sur l'analyse (peu de lignes, absence de colonne date/coût/montant...) et la colonne à ajouter pour l'améliorer.
N'annonce AUCUNE tendance ni cause que les données fournies ne montrent pas. Aucun conseil générique.

RÈGLES DE FORME :
- Réponds UNIQUEMENT avec un JSON valide — zéro texte, zéro markdown, zéro backtick.

STRUCTURE :
{
  "insights": [
    {
      "id": "insight_1",
      "title": "Titre court (max 8 mots)",
      "description": "Question métier + ce qui sera calculé + décision éclairée (1-2 phrases).",
      "type": "trend|comparison|total|anomaly|distribution",
      "feasibility": "high|medium|low",
      "required_columns": ["col1", "col2"]
    }
  ],
  "summary": "Paragraphe de 3-5 phrases."
}"""


def _clean_suggested_insights(insights: list, sample: dict) -> list:
    """Cohérence avec le fichier : colonnes existantes, ids uniques, types compatibles, maximum 6."""
    columns = sample.get("columns", []) or []
    by_norm = {_norm(c): c for c in columns}
    numeric = set(sample.get("numeric_columns", []) or [])
    dates = set(sample.get("date_columns", []) or [])
    cleaned, seen_ids = [], set()

    for ins in insights:
        if not isinstance(ins, dict):
            continue
        req = []
        for c in ins.get("required_columns", []) or []:
            real = c if c in columns else by_norm.get(_norm(c))
            if real and real not in req:
                req.append(real)
        if not req:
            continue  # insight sur des colonnes inexistantes → inexploitable
        ins["required_columns"] = req
        if ins.get("type") == "trend" and not any(c in dates for c in req):
            ins["type"] = "comparison"
        if ins.get("type") == "anomaly" and not any(c in numeric for c in req):
            ins["type"] = "comparison"
        iid = ins.get("id")
        if not iid or iid in seen_ids:
            iid = f"insight_{len(cleaned) + 1}"
        ins["id"] = iid
        seen_ids.add(iid)
        cleaned.append(ins)
    return cleaned[:6]


def get_analysis_instructions(sample: dict) -> dict:
    client = get_openai_client()
    stats_lines = []
    for col, stat in sample.get("stats_summary", {}).items():
        if isinstance(stat, dict):
            if "mean" in stat:
                stats_lines.append(
                    f"  {col}: moyenne={stat.get('mean')}, médiane={stat.get('median')}, "
                    f"min={stat.get('min')}, max={stat.get('max')}")
            elif "top_values" in stat:
                top = stat.get("top_values", [])[:3]
                stats_lines.append(f"  {col}: {stat.get('unique_count')} valeurs uniques, top: {', '.join(str(v) for v in top)}")

    hints = _column_hints(sample.get("columns", []), sample.get("numeric_columns", []))
    n_rows = sample.get("row_count", 0) or 0
    small = "\n⚠ Petit fichier (moins de 30 lignes) : reste prudent sur la faisabilité et signale-le dans le summary." if n_rows < 30 else ""

    user_msg = f"""Dataset :
- Lignes : {n_rows:,}
- Colonnes : {sample.get('columns', [])}
- Numériques : {sample.get('numeric_columns', [])}
- Catégorielles : {sample.get('categorical_columns', [])}
- Dates : {sample.get('date_columns', [])}
{small}

Nature probable des colonnes numériques (déduite des noms) :
{hints or '  (aucune indication)'}

Statistiques :
{chr(10).join(stats_lines) or '  (non disponibles)'}

Échantillon (10 lignes) :
{json.dumps(sample.get('sample_rows', [])[:10], indent=2, ensure_ascii=False, default=str)}

Réponds UNIQUEMENT avec le JSON."""

    result = _chat_json(client, SUGGEST_SYSTEM_PROMPT, user_msg, temperature=0.2, max_tokens=2000)
    result.setdefault("insights", [])
    result.setdefault("summary", "")
    cleaned = _clean_suggested_insights(result["insights"], sample)
    # Filet de sécurité : si le filtrage vide tout, on garde la réponse brute plutôt que rien
    result["insights"] = cleaned if cleaned else result["insights"][:6]
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
# Python a déjà calculé les agrégats ET les faits (parts, écarts, tendances,
# fiabilité). L'IA interprète en langage métier et recommande des actions.
# ============================================================================

FINAL_REPORT_SYSTEM_PROMPT = """Tu es un analyste BI senior qui travaille pour le dirigeant d'une PME. Il n'a pas de data analyst : ton rapport doit lui dire CE QUI SE PASSE dans son activité et CE QU'IL DOIT FAIRE, avec des chiffres.
Tu reçois : (1) des FAITS calculés par Python (fiables : à citer tels quels), (2) les données agrégées détaillées (pour construire les graphiques), (3) les analyses choisies par l'utilisateur. Produis un rapport JSON pour un dashboard interactif Chart.js.

══════════ PRINCIPES ══════════
1. Déduis d'abord le contexte métier des noms de colonnes (ventes, stock, RH, scolarité, finance...) et parle comme ce métier : « chiffre d'affaires », « clients », « références », « élèves »...
2. Chaque phrase d'analyse contient un CHIFFRE tiré des faits (valeur, %, écart, rang) et NOMME l'entité concernée (le produit, la ville, la période). INTERDIT : les formulations génériques sans chiffre ni entité (« améliorer la qualité », « optimiser les ventes », « surveiller l'évolution », « mettre en place une stratégie », « il est important de », « il serait pertinent de »).
3. Chaque recommandation = ACTION précise + ENTITÉ + CHIFFRE + pourquoi. Modèle : « Relancer en priorité [entité] : [chiffre] alors que [comparaison chiffrée] ; objectif : [cible mesurable]. »
4. AUCUNE INVENTION. Seuls chiffres autorisés : ceux des faits/données, et des calculs simples dessus (somme, différence, ratio, part en %), arrondis. Une cause supposée s'écrit comme hypothèse : « Hypothèse à vérifier : ... ».
5. Honnêteté statistique : si les faits signalent un petit échantillon, une corrélation peu fiable, une série trop courte ou une forte dispersion, dis-le en une phrase et ne tire pas de conclusion forte. Ne parle jamais de « tendance » pour une série de moins de 4 points. Une corrélation n'est pas une causalité.
6. Si une information nécessaire manque dans le fichier, dis PRÉCISÉMENT quelle colonne ajouter (ex. : « ajoutez une colonne Coût pour calculer la marge ») au lieu de combler avec du général.

══════════ AGRÉGATION ══════════
- MOYENNE (mean_values) : prix unitaire, tarif, taux, %, note, score, âge, satisfaction, délai, durée.
- SOMME (sum_values) : montant, chiffre d'affaires, ventes, coût, budget, salaire, quantité vendue, volume, stock.
- NOMBRE (count_values) : nombre d'opérations / clients / élèves par catégorie.
- Jamais d'addition de prix unitaires, taux ou notes.
- Les champs « recommended_* » viennent des noms de colonnes et peuvent se tromper : en cas de désaccord avec la NATURE réelle de la colonne, la nature réelle prime (les deux valeurs sont fournies). Les FAITS indiquent l'agrégation retenue.
- Une ligne « CA ESTIMÉ » des faits est une ESTIMATION : écris toujours « estimation » à côté du chiffre et recommande d'ajouter une colonne Montant/CA pour un résultat exact. Ne calcule pas d'autre CA toi-même.

══════════ KPIs (3 à 5) ══════════
- Ce qu'un dirigeant de ce métier suit réellement : totaux (CA, volumes), prix/taux/notes moyens, part du n°1 (concentration), écart entre meilleur et moins bon. Pas de KPI sur un identifiant, pas de « nombre de colonnes ».
- value : chiffre formaté avec unité (espace pour les milliers, virgule décimale : « 1 350 000 FCFA », « 12,3 / 20 », « 73 % »). raw_value : nombre. unit : unité (vide si déjà dans value).
- description (1-2 phrases) : ce que ça mesure ET comment le lire (comparer à la médiane, au maximum ou à la moyenne fournis).
- trend / trend_value : UNIQUEMENT si un fait « Évolution » correspond à ce KPI (reprends son % et sa période) ; sinon null.

══════════ GRAPHIQUES (2 à 4, jamais deux disant la même chose) ══════════
- title = la CONCLUSION (ex. : « Les Ordinateurs font 96 % du chiffre d'affaires »), pas « Répartition par produit ».
- description (2 phrases) : ce qu'il faut lire + l'écart chiffré. insight (1 phrase) : la DÉCISION qu'il éclaire.
- bar : catégories triées de la plus grande à la plus petite, 10 maximum. line : seulement avec une série temporelle d'au moins 4 points. doughnut : 6 catégories maximum (regrouper le reste). pie : 5 maximum.
- scatter : uniquement si |r| ≥ 0,4 sur au moins 10 points, 60 points maximum.
- Les valeurs de data.datasets[].data sont EXACTEMENT celles des données (bon champ mean/sum/count) ; même longueur que labels. Préfère moins de graphiques à un graphique sans valeur.
- bar/line : "label" du dataset = grandeur + unité (ex. « Total quantités »).

══════════ INSIGHTS ══════════
- Un insight par analyse sélectionnée par l'utilisateur, avec le même id et le même titre. Les analyses sont reprises dans « INSIGHTS SÉLECTIONNÉS » avec leurs colonnes.
- description : le constat chiffré (2-3 phrases). value : le chiffre clé en une ligne (ex. : « 1 350 000 FCFA — 96 % du CA »).
- interpretation : « Action : ... Pourquoi : ... À vérifier : ... » (action = quoi, sur quelle entité, avec quel objectif chiffré).
- Si l'analyse ne peut pas être calculée avec les données reçues : value = « Données insuffisantes », description = ce qui manque exactement, interpretation = la colonne à ajouter dans le fichier.
- data : toujours {}.

══════════ RÉSUMÉ ══════════
- title : le constat principal chiffré (12 mots max).
- description (4-5 phrases) : 1) constat principal chiffré ; 2) second constat ou écart important ; 3) principal risque ou opportunité ; 4) la première action à lancer, avec entité et objectif ; 5) la limite des données si elle compte.
- key_takeaways : exactement 3, au format « Constat chiffré → Action concrète ».

══════════ DEVISE ══════════
- Uniquement pour les colonnes monétaires (montant, prix, CA, coût, salaire, budget, frais) : devise détectée dans les données (€, $, £...), sinon FRANC CFA (FCFA). Jamais de devise sur des quantités, taux ou notes.

══════════ FORMAT ══════════
- Réponds UNIQUEMENT avec un JSON valide — zéro texte, zéro markdown, zéro backtick.
- Textes courts et denses : phrases directes, pas de phrases d'introduction.

STRUCTURE OBLIGATOIRE :
{
  "summary": {
    "title": "Constat principal chiffré",
    "description": "4-5 phrases.",
    "key_takeaways": [
      "Constat chiffré → Action 1",
      "Constat chiffré → Action 2",
      "Constat chiffré → Action 3"
    ]
  },
  "kpis": [
    {
      "id": "kpi_1",
      "label": "Nom du KPI (vocabulaire métier)",
      "value": "Valeur formatée avec unité",
      "raw_value": 12.35,
      "unit": "FCFA | % | unité | (vide si déjà dans value)",
      "trend": "up|down|stable|null",
      "trend_value": "ex: +28,6 % (2026-01-04 → 2026-03-01) ou null",
      "description": "Ce que ça mesure + comment le lire",
      "icon": "euro|percent|users|chart|trending-up|trending-down|target|clock|package|star"
    }
  ],
  "charts": [
    {
      "id": "chart_1",
      "type": "bar|line|pie|doughnut|scatter",
      "title": "Conclusion du graphique",
      "description": "2 phrases : lecture + écart chiffré.",
      "insight": "La décision éclairée, en 1 phrase.",
      "data": {
        "labels": ["Label1", "Label2", "Label3"],
        "datasets": [
          {
            "label": "Grandeur + unité",
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
      "title": "Titre de l'analyse sélectionnée",
      "description": "Constat chiffré.",
      "value": "Chiffre clé",
      "type": "trend|comparison|total|anomaly|distribution",
      "interpretation": "Action : ... Pourquoi : ... À vérifier : ...",
      "data": {}
    }
  ]
}

TYPES DE GRAPHIQUES :
- scatter : "data" ne contient PAS "labels". Contient uniquement : {"datasets": [{"label": "...", "data": [{"x": 1.2, "y": 3.4}, ...]}]}
- pie/doughnut : "data" = {"labels": [...], "datasets": [{"data": [...nombres...]}]} — PAS de "label" dans le dataset"""


def _sanitize_report(result: dict) -> dict:
    """Écarte ce qui casserait le dashboard : graphiques mal formés, KPIs sans valeur."""
    ok_charts = []
    for ch in result.get("charts", []):
        if not isinstance(ch, dict):
            continue
        t = ch.get("type")
        data = ch.get("data") or {}
        ds = data.get("datasets")
        if t not in {"bar", "line", "pie", "doughnut", "scatter"} or not isinstance(ds, list) or not ds:
            print(f"⚠️ Graphique écarté (type/datasets invalides) : {ch.get('title')!r}")
            continue
        try:
            if t == "scatter":
                pts = ds[0].get("data") if isinstance(ds[0], dict) else None
                if not pts or not all(isinstance(p, dict) and "x" in p and "y" in p for p in pts):
                    raise ValueError("points scatter invalides")
            else:
                labels = data.get("labels")
                if not isinstance(labels, list) or not labels:
                    raise ValueError("labels manquants")
                for d in ds:
                    vals = d.get("data") if isinstance(d, dict) else None
                    if not isinstance(vals, list) or len(vals) != len(labels):
                        raise ValueError("longueur data ≠ labels")
                    coerced = []
                    for v in vals:
                        if isinstance(v, str):
                            v = float(v.replace(" ", "").replace(",", "."))
                        if isinstance(v, bool) or not isinstance(v, (int, float)):
                            raise ValueError("valeur non numérique")
                        coerced.append(v)
                    d["data"] = coerced
        except (ValueError, TypeError) as e:
            print(f"⚠️ Graphique écarté ({e}) : {ch.get('title')!r}")
            continue
        ok_charts.append(ch)
    result["charts"] = ok_charts[:4]

    result["kpis"] = [k for k in result.get("kpis", [])
                      if isinstance(k, dict) and k.get("value") not in (None, "")][:5]
    result["insights"] = [i for i in result.get("insights", []) if isinstance(i, dict)]
    return result


def _collect_numbers(obj, acc: list):
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _collect_numbers(v, acc)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        acc.append(float(obj))


def _audit_chart_numbers(result: dict, aggregated_data: dict):
    """Journalise les valeurs de graphiques absentes des agrégats Python (aide au diagnostic)."""
    try:
        known = []
        _collect_numbers(aggregated_data, known)
        for ch in result.get("charts", []):
            if ch.get("type") == "scatter":
                continue
            for d in (ch.get("data") or {}).get("datasets", []):
                bad = [v for v in d.get("data", [])
                       if not any(abs(v - k) <= max(0.011, 0.005 * abs(k)) for k in known)]
                if bad:
                    print(f"⚠️ Graphique {ch.get('id')!r} : {len(bad)} valeur(s) absente(s) des agrégats "
                          f"(ex. {bad[:3]}) — à vérifier")
    except Exception:
        pass


def generate_final_report(aggregated_data: dict, selected_insights: list, metadata: dict) -> dict:
    client = get_openai_client()

    numeric_cols    = metadata.get("numeric_columns", [])
    categorical_cols = metadata.get("categorical_columns", [])
    text_cols       = metadata.get("text_columns", [])
    date_cols       = metadata.get("date_columns", [])
    statistics      = metadata.get("statistics", {})

    # Résumé stats global pour contexte
    stats_lines = []
    all_cols = numeric_cols + [c for c in (categorical_cols + text_cols)
                               if statistics.get(c, {}).get("type") in ["text", "categorical"]]
    for col in all_cols[:12]:
        stat = statistics.get(col, {})
        if stat.get("type") in ["integer", "float"]:
            agg = _best_aggregation_label(col)
            stats_lines.append(
                f"  {col} [{agg}]: moyenne={stat.get('mean')}, médiane={stat.get('median')}, min={stat.get('min')}, "
                f"max={stat.get('max')}, total={stat.get('sum')}"
            )
        elif stat.get("type") in ["text", "categorical"]:
            top = stat.get("top_values", [])[:3]
            top_str = ", ".join(f"{v['value']}={v['pct']}%" for v in top)
            stats_lines.append(f"  {col}: {stat.get('unique_count')} valeurs → {top_str}")

    insights_str = "\n".join([
        f"  [{ins.get('id','?')}] [{ins.get('type','?')}] {ins.get('title','')}: {ins.get('description','')} "
        f"(colonnes : {', '.join(ins.get('required_columns', []) or [])})"
        for ins in selected_insights
    ])

    facts = _build_facts_digest(aggregated_data, metadata)
    payload = json.dumps(_trim_for_prompt(aggregated_data), ensure_ascii=False, separators=(",", ":"), default=str)

    user_msg = f"""DATASET :
- {metadata.get('row_count', 0):,} lignes | {metadata.get('column_count', 0)} colonnes
- Colonnes numériques : {numeric_cols}
- Colonnes catégorielles / texte : {categorical_cols + text_cols}
- Colonnes dates : {date_cols}

FAITS CALCULÉS PAR PYTHON (fiables — cite-les, ne les recalcule pas) :
{facts}

STATISTIQUES GLOBALES :
{chr(10).join(stats_lines) or '  (non disponibles)'}

INSIGHTS SÉLECTIONNÉS PAR L'UTILISATEUR (un insight de sortie par ligne, même id) :
{insights_str or '  (aucun — propose 3 insights utiles à partir des faits)'}

══════════════════════════════════════════════════════
DONNÉES AGRÉGÉES DÉTAILLÉES (valeurs EXACTES à utiliser pour les graphiques)
Les « recommended_* » sont des suggestions : la nature réelle de la colonne prime (voir faits).
══════════════════════════════════════════════════════
{payload}

Génère le rapport JSON complet. Réponds UNIQUEMENT avec le JSON."""

    result = _chat_json(client, FINAL_REPORT_SYSTEM_PROMPT, user_msg, temperature=0.15, max_tokens=5000)
    result.setdefault("summary", {"title": "Rapport d'analyse", "description": "", "key_takeaways": []})
    result.setdefault("kpis", [])
    result.setdefault("charts", [])
    result.setdefault("insights", [])
    if not isinstance(result["summary"], dict):
        result["summary"] = {"title": "Rapport d'analyse", "description": str(result["summary"]), "key_takeaways": []}
    result["summary"].setdefault("title", "Rapport d'analyse")
    result["summary"].setdefault("description", "")
    result["summary"].setdefault("key_takeaways", [])

    result = _sanitize_report(result)
    _audit_chart_numbers(result, aggregated_data)

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


# ============================================================================
# VALIDATION INSIGHT PERSONNALISÉ
# ============================================================================

VALIDATE_INSIGHT_PROMPT = """Tu es un analyste BI senior. Un utilisateur formule une analyse en langage libre ; tu dis si elle est réalisable avec les colonnes du fichier et tu la reformules en question métier précise.

RÈGLES :
- valid=true seulement si l'analyse peut être calculée avec les colonnes listées : dimension + mesure (comparaison, total, répartition), date + mesure (évolution), ou 2 mesures (relation).
- Interprète avec bienveillance les formulations vagues ou le vocabulaire approximatif en choisissant les colonnes les plus pertinentes ; ne refuse pas pour une simple question de mots.
- required_columns : noms EXACTS parmi les colonnes fournies (1 à 3). Jamais un identifiant comme mesure.
- type : "trend" exige une colonne date ; "anomaly" exige une colonne numérique ; sinon "comparison", "total" ou "distribution" — toujours cohérent avec les colonnes choisies.
- reformulated_title : 8 mots max, formulé comme un constat à vérifier. reformulated_description (1-2 phrases) : la question métier + ce qui sera calculé + la décision éclairée.
- feasibility : "high" (colonnes exploitables), "medium" (possible mais limité), "low" (partiel).
- Si valid=false : "reason" (en français) dit PRÉCISÉMENT quelle donnée manque (ex. : « il n'y a pas de colonne de coût, la marge ne peut pas être calculée ») ET propose l'analyse la plus proche réalisable avec les colonnes existantes.

Retourne UNIQUEMENT un JSON valide (sans markdown) :
{
  "valid": true | false,
  "reformulated_title": "Titre court (si valid=true)",
  "reformulated_description": "Description reformulée (si valid=true)",
  "required_columns": ["col1", "col2"],
  "type": "trend|comparison|total|anomaly|distribution",
  "feasibility": "high|medium|low",
  "reason": "Explication + alternative si non réalisable (null si valid=true)"
}"""


def validate_custom_insight(description: str, columns: list, column_types: dict) -> dict:
    client = get_openai_client()
    user_msg = f"""Demande : "{description}"

Colonnes (nom → type) :
{json.dumps({col: column_types.get(col, 'text') for col in columns}, ensure_ascii=False, indent=2)}

Nature probable des colonnes numériques :
{_column_hints(columns, [c for c in columns if column_types.get(c) in ('integer', 'float')]) or '  (aucune indication)'}"""

    result = _chat_json(client, VALIDATE_INSIGHT_PROMPT, user_msg, temperature=0.2, max_tokens=500)
    result.setdefault("valid", False)
    result.setdefault("reformulated_title", None)
    result.setdefault("reformulated_description", None)
    result.setdefault("required_columns", [])
    result.setdefault("type", "comparison")
    result.setdefault("feasibility", "medium")
    result.setdefault("reason", None)

    # Cohérence avec le fichier : colonnes réelles uniquement
    by_norm = {_norm(c): c for c in columns}
    req = []
    for c in result.get("required_columns") or []:
        real = c if c in columns else by_norm.get(_norm(c))
        if real and real not in req:
            req.append(real)
    result["required_columns"] = req
    if result["valid"] and not req:
        result["valid"] = False
        result["reason"] = result.get("reason") or (
            "Aucune colonne du fichier ne correspond à cette analyse. Reformulez avec le nom d'une colonne existante.")
    if result["valid"] and result.get("type") == "trend" and not any(
            column_types.get(c) == "date" for c in req):
        result["type"] = "comparison"

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

    kpi_str = "\n".join([f"  - {k.get('label', '')}: {k.get('value', '')}" for k in kpis[:5]])
    ins_str = "\n".join([f"  - {i.get('title', '')}: {i.get('value', '')} | {i.get('interpretation', '')}"
                         for i in insights[:5]])

    context = f"""Tu es un analyste BI senior qui répond au dirigeant d'une PME.
Rapport : {summary.get('title', 'Analyse')}
Résumé : {summary.get('description', '')}
KPIs : {kpi_str or '(aucun)'}
Insights : {ins_str or '(aucun)'}
Colonnes du fichier : {metadata.get('columns', [])}

RÈGLES : réponds en français, en 5 phrases maximum. Commence par la réponse directe avec les chiffres du rapport ci-dessus.
Termine par l'action concrète à mener (quoi, sur quelle entité). N'invente aucun chiffre : si la réponse n'est pas dans le rapport,
dis-le et précise l'analyse ou la colonne nécessaire pour y répondre."""

    response = client.chat.completions.create(
        model=MODEL,
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