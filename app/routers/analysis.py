"""
ROUTES D'ANALYSE

Workflow en 2 étapes :
1. POST /analysis/upload
   → Lit le fichier, génère les métadonnées
   → Appelle GPT-4o-mini IMMÉDIATEMENT (timeout 3 min)
   → Stocke les insights suggérés dans results["suggested_insights"]
   → Crée l'analyse en PENDING
   → Retourne métadonnées + insights au frontend

2. POST /analysis/{id}/confirm
   → Vérifie le solde, déduit les tokens
   → Lance le pipeline de graphiques en background (utilise les insights choisis)

Autres endpoints :
- POST /analysis/validate          → Validation structure fichier
- POST /analysis/validate-insight  → Validation insight via OpenAI
- GET  /analysis/                  → Liste des analyses
- POST /analysis/{id}/ask          → Question IA sur une analyse
- GET  /analysis/{id}              → Résultats d'une analyse
- DELETE /analysis/{id}            → Suppression
"""

import json
import threading
from pathlib import Path
from datetime import datetime
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Request
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.database.models import User, Analysis, AnalysisStatus
from app.database.session import get_db
from app.schemas.analysis import UploadResponse, AnalysisResult, AnalysisListItem
from app.services.analysis_service import run_analysis_pipeline

router = APIRouter(prefix="/analysis", tags=["Analysis"])

TEMP_DIR = Path("data/temp")
UPLOAD_DIR = Path("data/uploads")
RESULTS_DIR = Path("data/results")
TEMP_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}
MAX_FILE_SIZE = 10 * 1024 * 1024


def calculate_token_cost(file_size_bytes: int) -> int:
    size_ko = file_size_bytes / 1024
    if size_ko <= 10:
        return 1
    return ceil(size_ko / 10)


# ============================================================================
# VALIDATE
# ============================================================================

@router.post("/validate")
async def validate_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return {"valid": False, "errors": [f"Format non supporté : {ext}."],
                "warnings": [], "sheet_count": 0, "row_count": 0,
                "column_count": 0, "file_name": file.filename, "file_size": 0}

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        return {"valid": False, "errors": ["Fichier trop volumineux. Maximum 10 MB."],
                "warnings": [], "sheet_count": 0, "row_count": 0,
                "column_count": 0, "file_name": file.filename, "file_size": len(content)}

    temp_path = TEMP_DIR / f"validate_{current_user.id}_{file.filename}"
    with open(temp_path, "wb") as f_out:
        f_out.write(content)
    try:
        from app.services.excel_service import validate_file_structure
        result = validate_file_structure(str(temp_path))
        return {**result, "file_name": file.filename, "file_size": len(content)}
    finally:
        try:
            import gc; gc.collect()
            if temp_path.exists(): temp_path.unlink()
        except PermissionError:
            pass


# ============================================================================
# VALIDATE INSIGHT
# ============================================================================

@router.post("/validate-insight")
async def validate_custom_insight(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    body = await request.json()
    description = body.get("description", "").strip()
    analysis_id = body.get("analysis_id")

    if not description or len(description) < 5:
        return {"valid": False, "reason": "Description trop courte.", "error": None}

    columns, column_types = [], {}

    if analysis_id:
        a = db.query(Analysis).filter(Analysis.id == analysis_id, Analysis.user_id == current_user.id).first()
        if a and a.results:
            meta = a.results.get("metadata", {})
            columns = meta.get("columns", [])
            column_types = {c: i.get("semantic_type", "text") for c, i in meta.get("column_types", {}).items()}

    if not columns:
        latest = db.query(Analysis).filter(
            Analysis.user_id == current_user.id,
            Analysis.status == AnalysisStatus.COMPLETED
        ).order_by(Analysis.created_at.desc()).first()
        if latest and latest.results:
            meta = latest.results.get("metadata", {})
            columns = meta.get("columns", [])
            column_types = {c: i.get("semantic_type", "text") for c, i in meta.get("column_types", {}).items()}

    if not columns:
        return {"valid": False, "reason": "Impossible de récupérer les colonnes.", "error": None}

    from app.services.openai_service import validate_custom_insight as ai_validate
    try:
        return ai_validate(description, columns, column_types)
    except Exception as e:
        raise HTTPException(status_code=504, detail=f"Service IA indisponible : {str(e)}")


# ============================================================================
# LISTE DES ANALYSES
# ============================================================================

@router.get("/", response_model=list[AnalysisListItem])
async def list_analyses(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    analyses = (
        db.query(Analysis)
        .filter(Analysis.user_id == current_user.id)
        .order_by(Analysis.created_at.desc())
        .all()
    )
    return [
        AnalysisListItem(
            id=a.id, file_name=a.file_name,
            status=a.status.value, tokens_consumed=a.tokens_consumed,
            created_at=a.created_at.isoformat() if a.created_at else None
        )
        for a in analyses
    ]


# ============================================================================
# UPLOAD — ÉTAPE 1/2
# Lit le fichier → génère métadonnées → appelle GPT-4o-mini (max 3 min)
# → stocke les insights suggérés → crée l'analyse PENDING
# → retourne métadonnées + insights au frontend (étape 3 prête immédiatement)
# ============================================================================

@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Format non supporté : {ext}.")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="Fichier trop volumineux. Maximum 10 MB.")

    original_stem = Path(file.filename).stem
    excel_path = TEMP_DIR / file.filename
    json_path = UPLOAD_DIR / f"{original_stem}.json"

    with open(excel_path, "wb") as f_out:
        f_out.write(content)

    try:
        from app.services.excel_service import process_uploaded_file, create_sample
        from app.services.openai_service import get_analysis_instructions

        # 1. Lecture et conversion Excel → JSON + métadonnées
        processed = process_uploaded_file(str(excel_path), json_path)
        metadata = processed["metadata"]
        sample = processed.get("sample") or create_sample(processed.get("df"), metadata)

        # 2. Appel OpenAI immédiat (timeout 3 min côté client OpenAI)
        print(f"🤖 Appel GPT-4o-mini pour le fichier {file.filename}...")
        try:
            ai_instructions = get_analysis_instructions(sample)
        except Exception as ai_err:
            # Nettoyage fichier json avant de lever l'erreur
            try:
                if json_path.exists(): json_path.unlink()
            except Exception:
                pass
            raise HTTPException(
                status_code=504,
                detail=f"L'IA n'a pas répondu dans les 3 minutes. Veuillez réessayer. ({str(ai_err)})"
            )

        suggested_insights = ai_instructions.get("insights", [])
        print(f"✅ {len(suggested_insights)} insights générés par GPT-4o-mini")

        # 3. Création de l'analyse PENDING avec les insights déjà stockés
        analysis = Analysis(
            user_id=current_user.id,
            file_name=file.filename,
            file_size=len(content),
            file_path=str(json_path),
            status=AnalysisStatus.PENDING,
            tokens_consumed=0,
            # On stocke les insights suggérés + instructions pour le pipeline
            results={
                "metadata": metadata,
                "suggested_insights": suggested_insights,
                "ai_instructions": ai_instructions,
            }
        )
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

        print(f"📁 Upload #{analysis.id} prêt ({calculate_token_cost(len(content))} jeton(s) requis)")

        return UploadResponse(
            analysis_id=analysis.id,
            dataset_id=original_stem,
            file_name=file.filename,
            status="pending",
            metadata=metadata,
            suggested_insights=suggested_insights,
            ai_summary=ai_instructions.get("summary", ""),
        )

    except HTTPException:
        raise
    except ValueError as e:
        try:
            if json_path.exists(): json_path.unlink()
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        try:
            if json_path.exists(): json_path.unlink()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Erreur conversion : {str(e)}")
    finally:
        try:
            import gc; gc.collect()
            if excel_path.exists(): excel_path.unlink()
        except PermissionError:
            pass


# ============================================================================
# CONFIRM — ÉTAPE 2/2
# L'utilisateur a choisi ses insights → on vérifie le solde, on déduit,
# on lance le pipeline graphiques en background.
# Pas de remboursement : si l'analyse échoue, les tokens sont consommés.
# ============================================================================

@router.post("/{analysis_id}/confirm")
async def confirm_analysis(
    analysis_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Confirmation du lancement après sélection des insights par l'utilisateur.
    - selected_insights : liste des IDs d'insights choisis (max 6)
    - Vérifie le solde et déduit les tokens
    - Lance le pipeline de graphiques en background
    """
    body = await request.json()
    selected_insight_ids: list = body.get("selected_insights", [])

    analysis = db.query(Analysis).filter(
        Analysis.id == analysis_id,
        Analysis.user_id == current_user.id
    ).first()

    if not analysis:
        raise HTTPException(status_code=404, detail="Analyse introuvable.")

    if analysis.status != AnalysisStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail="Cette analyse a déjà été lancée ou est terminée."
        )

    token_cost = calculate_token_cost(analysis.file_size or 0)

    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    if user.token_balance < token_cost:
        raise HTTPException(
            status_code=402,
            detail=f"Solde insuffisant. Requis : {token_cost} jeton(s), disponible : {user.token_balance}."
        )

    # Déduction IMMÉDIATE avant lancement
    from app.services.user_service import deduct_tokens
    deduct_tokens(db, user, token_cost)
    print(f"💰 {token_cost} jeton(s) déduit(s) pour user#{user.id}, analyse#{analysis.id}")

    # Filtrer les insights selon la sélection de l'utilisateur
    existing_results = analysis.results or {}
    ai_instructions = existing_results.get("ai_instructions", {})
    all_suggested = existing_results.get("suggested_insights", [])

    if selected_insight_ids:
        selected_insights = [
            ins for ins in all_suggested
            if ins.get("id") in selected_insight_ids
        ]
    else:
        # Si aucune sélection → on prend tous les insights suggérés
        selected_insights = all_suggested

    # Mise à jour du statut
    analysis.status = AnalysisStatus.PROCESSING
    analysis.tokens_consumed = token_cost
    # Enregistre la sélection finale
    existing_results["selected_insights"] = selected_insights
    analysis.results = existing_results
    db.commit()
    db.refresh(analysis)

    # Récupération du fichier de données
    json_path = Path(analysis.file_path)
    if not json_path.exists():
        analysis.status = AnalysisStatus.FAILED
        analysis.error_message = "Fichier de données introuvable."
        analysis.finished_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=500, detail="Fichier de données introuvable.")

    from app.services.excel_service import load_from_json, create_sample
    df, metadata = load_from_json(str(json_path))
    sample = create_sample(df, metadata)
    dataset_id = json_path.stem

    thread = threading.Thread(
        target=run_analysis_background,
        kwargs={
            "analysis_id": analysis.id,
            "dataset_id": dataset_id,
            "sample": sample,
            "metadata": metadata,
            "ai_instructions": ai_instructions,
            "selected_insights": selected_insights,
        },
        daemon=True
    )
    thread.start()
    print(f"🚀 Analyse #{analysis.id} lancée — {len(selected_insights)} insight(s) sélectionné(s)")

    return {
        "analysis_id": analysis.id,
        "status": "processing",
        "tokens_consumed": token_cost,
        "tokens_remaining": user.token_balance,
        "selected_insights_count": len(selected_insights),
    }


# ============================================================================
# TÂCHE DE FOND
# Les tokens sont déjà déduits dans /confirm.
# En cas d'échec → statut FAILED, pas de remboursement.
# ============================================================================

def run_analysis_background(
    analysis_id: int,
    dataset_id: str,
    sample: dict,
    metadata: dict,
    ai_instructions: dict,
    selected_insights: list,
):
    print(f"🔄 Background #{analysis_id}")

    from app.database.session import SessionLocal
    db = SessionLocal()
    analysis = None
    try:
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if not analysis:
            return

        analysis.started_at = datetime.utcnow()
        db.commit()

        print(f"📂 dataset_id reçu par background: {dataset_id!r}")
        print(f"📋 {len(selected_insights)} insights sélectionnés")
        print(f"🔑 ai_instructions keys: {list(ai_instructions.keys()) if ai_instructions else None}")

        # Lance le pipeline avec les instructions déjà générées (pas de nouvel appel OpenAI)
        results = run_analysis_pipeline(
            dataset_id=dataset_id,
            sample=sample,
            metadata=metadata,
            ai_instructions=ai_instructions,
            selected_insights=selected_insights,
        )

        # Fusionne avec les données déjà stockées (suggested_insights, etc.)
        existing = dict(analysis.results or {})
        existing.update(results)

        # Sauvegarde fichier résultat (optionnel, ne bloque pas si erreur)
        try:
            safe_name = Path(dataset_id).stem
            results_path = RESULTS_DIR / f"{safe_name}_results.json"
            with open(results_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2, default=str)
        except Exception as save_err:
            print(f"⚠️ Sauvegarde fichier ignorée : {save_err}")

        # IMPORTANT : SQLAlchemy ne détecte pas les mutations de colonnes JSON
        # Il faut réassigner un nouvel objet dict pour forcer le dirty-tracking
        from sqlalchemy.orm.attributes import flag_modified
        analysis.results = existing
        flag_modified(analysis, "results")
        analysis.status = AnalysisStatus.COMPLETED
        analysis.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(analysis)
        print(f"✅ Analyse #{analysis_id} terminée — {len(existing.get('charts') or [])} graphiques, {len(existing.get('kpis') or [])} KPIs")

    except Exception as e:
        print(f"❌ Erreur #{analysis_id}: {e}")
        import traceback; traceback.print_exc()

        if analysis:
            analysis.status = AnalysisStatus.FAILED
            analysis.error_message = str(e)
            analysis.finished_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


# ============================================================================
# QUESTION IA
# ============================================================================

@router.post("/{analysis_id}/ask")
async def ask_question(
    analysis_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    body = await request.json()
    question = body.get("question", "").strip()

    if not question:
        raise HTTPException(status_code=400, detail="Question vide.")

    analysis = db.query(Analysis).filter(
        Analysis.id == analysis_id,
        Analysis.user_id == current_user.id
    ).first()

    if not analysis:
        raise HTTPException(status_code=404, detail="Analyse introuvable.")
    if analysis.status != AnalysisStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="L'analyse n'est pas encore terminée.")

    results = analysis.results or {}
    metadata = results.get("metadata", {})
    ai_instructions = results.get("ai_instructions", {})
    insights = results.get("insights", [])

    context = f"""Tu es un assistant expert en analyse de données.
Fichier : {analysis.file_name}
Lignes : {metadata.get('row_count', 'N/A')} | Colonnes : {metadata.get('columns', [])}
Numériques : {metadata.get('numeric_columns', [])} | Catégorielles : {metadata.get('categorical_columns', [])}
Résumé : {ai_instructions.get('summary', 'Non disponible')}
Insights : {chr(10).join([f"- {i.get('title','')}: {i.get('value','')}" for i in insights[:5]])}
Réponds en français, de manière claire et concise."""

    try:
        from app.services.openai_service import get_openai_client
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": context}, {"role": "user", "content": question}],
            temperature=0.3, max_tokens=800,
        )
        return {"answer": response.choices[0].message.content.strip(), "service_unavailable": False}

    except Exception as e:
        msg = str(e).lower()
        if "quota" in msg or "429" in msg:
            detail = "Quota OpenAI épuisé."
        elif "api_key" in msg or "authentication" in msg:
            detail = "Service IA indisponible."
        elif "connection" in msg:
            detail = "Impossible de contacter le service IA."
        else:
            detail = "Service IA temporairement indisponible."
        return {"answer": None, "service_unavailable": True, "detail": detail}


# ============================================================================
# GET ANALYSE
# ============================================================================

@router.get("/{analysis_id}", response_model=AnalysisResult)
async def get_analysis(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analyse introuvable")
    if analysis.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Accès refusé")

    results = analysis.results or {}
    return AnalysisResult(
        analysis_id=analysis.id,
        dataset_id=Path(analysis.file_path).stem if analysis.file_path else "",
        file_name=analysis.file_name,
        status=analysis.status.value,
        metadata=results.get("metadata"),
        summary=results.get("summary"),
        kpis=results.get("kpis"),
        charts=results.get("charts"),
        insights=results.get("insights"),
        explanations=results.get("explanations"),
        ai_instructions=results.get("ai_instructions"),
        suggested_insights=results.get("suggested_insights"),
        tokens_consumed=analysis.tokens_consumed,
        created_at=analysis.created_at.isoformat() if analysis.created_at else None
    )


# ============================================================================
# DELETE ANALYSE
# ============================================================================

@router.delete("/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_analysis(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analyse introuvable")
    if analysis.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Accès refusé")

    if analysis.file_path:
        p = Path(analysis.file_path)
        if p.exists(): p.unlink()

    db.delete(analysis)
    db.commit()