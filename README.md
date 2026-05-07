# AutoBI — Backend

> API REST FastAPI pour l'analyse automatique de fichiers Excel/CSV par intelligence artificielle.  
> Le backend orchestre l'upload, l'analyse IA (GPT-4o-mini), la gestion des jetons et la génération de rapports JSON complets.

---

## Stack technique

| Composant | Technologie |
|---|---|
| Framework API | FastAPI (Python 3.11+) |
| Base de données | MySQL + SQLAlchemy ORM |
| IA | OpenAI GPT-4o-mini |
| Authentification | JWT (Bearer token) |
| Traitement données | Pandas + NumPy |
| Serveur | Uvicorn |

---

## Structure du projet

```
backend/
├── app/
│   ├── core/
│   │   ├── config.py          # Variables d'environnement (Settings Pydantic)
│   │   └── security.py        # Hash mot de passe, création JWT
│   ├── database/
│   │   ├── models.py          # Modèles SQLAlchemy (User, Analysis, TokenTransaction)
│   │   └── session.py         # Connexion MySQL, get_db()
│   ├── dependencies/
│   │   └── auth.py            # get_current_user() — dépendance JWT
│   ├── routers/
│   │   ├── auth.py            # Routes /auth/*
│   │   └── analysis.py        # Routes /analysis/*
│   ├── schemas/
│   │   ├── auth.py            # LoginRequest, RegisterRequest, AuthResponse
│   │   ├── user.py            # UserResponse, UserProfile
│   │   └── analysis.py        # UploadResponse, AnalysisResult, AnalysisListItem
│   └── services/
│       ├── user_service.py    # CRUD utilisateur, gestion jetons
│       ├── excel_service.py   # Lecture/validation Excel, extraction statistiques
│       ├── analysis_service.py # Pipeline Pandas — agrégations + données pour l'IA
│       └── openai_service.py  # Appels GPT-4o-mini (suggestions + rapport final)
├── data/
│   ├── temp/                  # Fichiers Excel temporaires (supprimés après traitement)
│   ├── uploads/               # Datasets convertis en JSON
│   └── results/               # Rapports JSON finaux (backup fichier)
├── .env                       # Variables d'environnement (ne pas commiter)
├── requirements.txt
└── main.py                    # Point d'entrée FastAPI
```

---

## Variables d'environnement (.env)

```env
DATABASE_URL=mysql+pymysql://user:password@localhost:3306/autobi
SECRET_KEY=votre_clé_secrète_jwt
ACCESS_TOKEN_EXPIRE_MINUTES=1440
OPENAI_API_KEY=sk-proj-...
DEFAULT_TOKEN_BALANCE=5
DEBUG=false
```

---

## Modèles de base de données

### `users`
| Champ | Type | Description |
|---|---|---|
| id | INT PK | Identifiant unique |
| email | VARCHAR(255) UNIQUE | Email de connexion |
| password_hash | VARCHAR(255) | Mot de passe bcrypt |
| first_name | VARCHAR(100) | Prénom |
| last_name | VARCHAR(100) | Nom |
| plan_type | ENUM | `PAY_AS_YOU_GO` \| `MONTHLY_UNLIMITED` |
| token_balance | INT | Solde de jetons (min 0) |
| created_at | DATETIME | Date d'inscription |
| last_login | DATETIME | Dernière connexion |

### `analyses`
| Champ | Type | Description |
|---|---|---|
| id | INT PK | Identifiant unique |
| user_id | INT FK | Propriétaire |
| file_name | VARCHAR(255) | Nom du fichier uploadé |
| file_size | INT | Taille en octets |
| file_path | VARCHAR(500) | Chemin vers le JSON du dataset |
| status | ENUM | `PENDING` \| `PROCESSING` \| `COMPLETED` \| `FAILED` |
| results | JSON | Tout le rapport (metadata, summary, kpis, charts, insights) |
| tokens_consumed | INT | Jetons déduits pour cette analyse |
| error_message | TEXT | Message d'erreur si FAILED |
| created_at / started_at / finished_at | DATETIME | Timestamps du cycle de vie |

### `token_transactions`
| Champ | Type | Description |
|---|---|---|
| id | INT PK | Identifiant |
| user_id | INT FK | Utilisateur |
| transaction_type | VARCHAR | `purchase` \| `consumption` |
| amount | INT | Nombre de jetons |
| analysis_id | INT FK nullable | Analyse liée (si consumption) |
| balance_after | INT | Solde après transaction |

---

## API — Authentification `/auth`

### `POST /auth/register`
Inscription d'un nouvel utilisateur. Crée le compte avec 5 jetons gratuits et retourne un JWT.

**Body :**
```json
{
  "email": "user@example.com",
  "password": "motdepasse",
  "first_name": "Jean",
  "last_name": "Dupont"
}
```

**Réponse 201 :**
```json
{
  "user": { "id": 1, "email": "...", "token_balance": 5, ... },
  "token": "eyJ...",
  "expires_at": "2024-01-01T10:30:00Z"
}
```

---

### `POST /auth/login`
Connexion. Vérifie email + mot de passe, retourne un JWT.

**Body :** `{ "email": "...", "password": "..." }`

**Réponse 200 :** même structure que `/register`

---

### `GET /auth/me`
Retourne le profil complet de l'utilisateur connecté.

**Header :** `Authorization: Bearer <token>`

**Réponse 200 :**
```json
{
  "id": 1, "email": "...", "first_name": "...", "last_name": "...",
  "plan_type": "PAY_AS_YOU_GO", "token_balance": 3,
  "created_at": "...", "last_login": "..."
}
```

---

### `GET /auth/verify`
Vérifie la validité d'un token JWT.

**Réponse 200 :** `{ "valid": true, "user_id": 1, "email": "..." }`

---

## API — Analyse `/analysis`

> Toutes les routes nécessitent `Authorization: Bearer <token>`

---

### Workflow complet en 3 étapes

```
┌─────────────────────────────────────────────────────────────────┐
│  ÉTAPE 1 — Upload                                               │
│  POST /analysis/upload                                          │
│  → Lit le fichier Excel/CSV                                     │
│  → Appelle GPT-4o-mini (max 3 min) pour générer 4-6 insights   │
│  → Crée l'analyse en statut PENDING                             │
│  → Retourne metadata + suggested_insights (prêts immédiatement) │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  ÉTAPE 2 — Sélection (frontend uniquement)                      │
│  L'utilisateur choisit jusqu'à 6 insights parmi les suggestions │
│  Peut aussi ajouter des insights personnalisés via              │
│  POST /analysis/validate-insight                                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  ÉTAPE 3 — Confirmation                                         │
│  POST /analysis/{id}/confirm                                    │
│  → Vérifie et déduit les jetons                                 │
│  → Lance le pipeline en background (thread)                     │
│  → Pandas calcule les vraies agrégations                        │
│  → GPT-4o-mini génère le rapport final (charts, KPIs, insights) │
│  → Stocke en DB → statut COMPLETED                              │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  ÉTAPE 4 — Suivi & Résultat                                     │
│  GET /analysis/{id}                                             │
│  → Poll jusqu'à status = COMPLETED                              │
│  → Retourne le rapport JSON complet                             │
└─────────────────────────────────────────────────────────────────┘
```

---

### `POST /analysis/validate`
Valide la structure d'un fichier sans créer d'analyse. Utilisé à l'étape 1 du wizard.

**Body :** `multipart/form-data` avec le fichier

**Réponse :**
```json
{
  "valid": true,
  "file_name": "data.xlsx",
  "file_size": 45000,
  "sheet_count": 1,
  "row_count": 200,
  "column_count": 10,
  "errors": [],
  "warnings": ["15 valeurs manquantes détectées"]
}
```

---

### `POST /analysis/upload`
**Route principale.** Upload + appel OpenAI immédiat pour les suggestions d'insights.

- Formats acceptés : `.xlsx`, `.xls`, `.csv`
- Taille max : 10 Mo
- Timeout OpenAI : 180 secondes — si dépassé, HTTP 504 propre

**Body :** `multipart/form-data` avec le fichier

**Réponse 201 :**
```json
{
  "analysis_id": 47,
  "dataset_id": "mon_fichier",
  "file_name": "mon_fichier.xlsx",
  "status": "pending",
  "metadata": {
    "columns": ["Filiere", "Moyenne_Generale", "Frais_Scolarite", ...],
    "row_count": 200,
    "column_count": 10,
    "numeric_columns": ["Age", "Moyenne_Generale", "Frais_Scolarite"],
    "text_columns": ["Filiere", "Statut_Paiement", "Ville"],
    "statistics": { ... }
  },
  "suggested_insights": [
    {
      "id": "insight_1",
      "title": "Performance par Filière",
      "description": "Comparer la moyenne générale par filière.",
      "type": "comparison",
      "feasibility": "high",
      "required_columns": ["Filiere", "Moyenne_Generale"]
    }
  ],
  "ai_summary": "Ce dataset contient 200 étudiants..."
}
```

---

### `POST /analysis/validate-insight`
Valide et reformule un insight personnalisé écrit en langage naturel via GPT-4o-mini.

**Body :**
```json
{
  "description": "Je veux voir la performance des étudiants selon leur ville",
  "analysis_id": 47
}
```

**Réponse :**
```json
{
  "valid": true,
  "reformulated_title": "Performance académique par ville",
  "reformulated_description": "Comparaison de la moyenne générale des étudiants selon leur ville d'origine.",
  "required_columns": ["Ville", "Moyenne_Generale"],
  "type": "comparison",
  "feasibility": "high",
  "reason": null,
  "error": null
}
```

---

### `POST /analysis/{id}/confirm`
Lance l'analyse après sélection des insights. Déduit les jetons et démarre le pipeline en arrière-plan.

**Calcul du coût :** `ceil(taille_Ko / 10)` jetons, minimum 1

**Body :**
```json
{
  "selected_insights": ["insight_1", "insight_3", "insight_5"]
}
```

**Réponse :**
```json
{
  "analysis_id": 47,
  "status": "processing",
  "tokens_consumed": 2,
  "tokens_remaining": 3,
  "selected_insights_count": 3
}
```

**Erreurs possibles :**
- `402` : solde insuffisant
- `400` : analyse déjà lancée ou terminée
- `500` : fichier dataset introuvable

---

### `GET /analysis/{id}`
Retourne l'état et les résultats d'une analyse. Utilisé pour le polling et l'affichage du dashboard.

**Réponse (COMPLETED) :**
```json
{
  "analysis_id": 47,
  "file_name": "university.xlsx",
  "status": "COMPLETED",
  "tokens_consumed": 2,
  "metadata": { ... },
  "summary": {
    "title": "Analyse des Performances Académiques",
    "description": "Ce rapport examine...",
    "key_takeaways": ["Moyenne générale : 12.35", ...]
  },
  "kpis": [
    {
      "id": "kpi_1",
      "label": "Moyenne Générale",
      "value": "12.35",
      "raw_value": 12.35,
      "unit": "",
      "trend": "stable",
      "trend_value": null,
      "description": "Performance académique moyenne.",
      "icon": "chart"
    }
  ],
  "charts": [
    {
      "id": "chart_1",
      "type": "bar",
      "title": "Moyenne par Filière",
      "description": "Les étudiants en Informatique affichent la meilleure moyenne.",
      "insight": "Informatique domine avec 13.2/20.",
      "data": {
        "labels": ["Informatique", "Gestion", "Droit"],
        "datasets": [{ "label": "Moyenne", "data": [13.2, 12.1, 11.8] }]
      },
      "options": { "x_label": "Filière", "y_label": "Moyenne /20", "stacked": false, "show_legend": true }
    }
  ],
  "insights": [
    {
      "id": "insight_1",
      "title": "Performance par Filière",
      "description": "Écart de 1.4 points entre la meilleure et la moins bonne filière.",
      "value": "13.2/20 (Informatique) vs 11.8/20 (Droit)",
      "type": "comparison",
      "interpretation": "Renforcer le soutien pédagogique en Droit.",
      "data": {}
    }
  ],
  "suggested_insights": [ ... ],
  "ai_instructions": { ... },
  "created_at": "2026-05-06T21:00:00"
}
```

---

### `GET /analysis/`
Liste toutes les analyses de l'utilisateur connecté (ordre antéchronologique).

**Réponse :**
```json
[
  { "id": 47, "file_name": "university.xlsx", "status": "COMPLETED", "tokens_consumed": 2, "created_at": "..." },
  { "id": 46, "file_name": "ventes.xlsx", "status": "FAILED", "tokens_consumed": 0, "created_at": "..." }
]
```

---

### `POST /analysis/{id}/ask`
Pose une question en langage naturel sur une analyse terminée. Utilise GPT-4o-mini avec le contexte du rapport.

**Body :** `{ "question": "Quelle filière devrait être prioritaire ?" }`

**Réponse :**
```json
{
  "answer": "D'après les données, la filière Droit présente...",
  "service_unavailable": false
}
```

---

### `DELETE /analysis/{id}`
Supprime une analyse et son fichier JSON associé.

**Réponse :** `204 No Content`

---

## Services internes

### `excel_service.py`
- Lit `.xlsx`, `.xls`, `.csv` avec openpyxl / pandas
- Valide la structure (en-têtes, types, valeurs manquantes)
- Calcule les statistiques complètes par colonne (mean, median, std, min, max, top_values, etc.)
- Détecte automatiquement les types : numérique, texte, date, booléen
- Nettoie les valeurs manquantes (remplacement par moyenne pour numériques)
- Convertit le dataset en JSON pour stockage persistant

### `analysis_service.py`
Calcule les agrégations Pandas **avant** d'appeler OpenAI :
- **KPIs globaux** : mean, median, sum, min, max, std par colonne numérique
- **Groupby intelligent** : pour chaque combinaison catégorie × numérique, calcule `mean_values`, `sum_values`, `count_values` + recommande l'agrégation selon le type de colonne
- **Règle d'agrégation** : `moyenne/note/taux/age` → MEAN | `montant/frais/quantité` → SUM
- **Distributions** : top valeurs avec pourcentages pour les colonnes catégorielles
- **Corrélations** : coefficient + points scatter pour chaque paire de numériques
- **Évolution temporelle** : résample auto (jour/semaine/mois selon l'étendue)

### `openai_service.py`
Deux appels distincts à GPT-4o-mini :

**Appel 1 — `get_analysis_instructions(sample)`** (pendant l'upload)
- Reçoit un résumé compact du dataset (colonnes, stats, 10 lignes)
- Retourne 4-6 suggestions d'insights (titres + descriptions)
- Timeout : 180s

**Appel 2 — `generate_final_report(aggregated_data, selected_insights, metadata)`** (après confirm)
- Reçoit toutes les données agrégées par Pandas
- Retourne le JSON final complet : `summary`, `kpis[]`, `charts[]`, `insights[]`
- Les charts contiennent `data.labels` + `data.datasets` prêts pour Chart.js
- Devise : FCFA par défaut si aucune devise détectée dans les données

---

## Gestion des jetons

| Action | Jetons |
|---|---|
| Inscription | +5 offerts |
| Analyse ≤ 10 Ko | 1 jeton |
| Analyse 11–20 Ko | 2 jetons |
| Analyse N Ko | `ceil(N/10)` jetons |
| Achat (OpenPay) | variable |

Les jetons sont **déduits immédiatement** à la confirmation (`/confirm`). Il n'y a **aucun remboursement** en cas d'échec — l'analyse passe en statut `FAILED` avec un message d'erreur.

---

## Lancement

```bash
# Installation
pip install -r requirements.txt

# Variables d'environnement
cp .env.example .env
# Remplir DATABASE_URL, SECRET_KEY, OPENAI_API_KEY

# Lancement
uvicorn main:app --reload --port 8000

# Documentation interactive
http://localhost:8000/docs
```

---

## Codes d'erreur courants

| Code | Signification |
|---|---|
| 400 | Fichier invalide / analyse déjà lancée |
| 401 | Token JWT manquant ou expiré |
| 402 | Solde de jetons insuffisant |
| 403 | Accès à une ressource d'un autre utilisateur |
| 404 | Analyse introuvable |
| 504 | OpenAI n'a pas répondu dans les 3 minutes |
| 500 | Erreur serveur (voir logs uvicorn) |