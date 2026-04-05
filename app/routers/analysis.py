"""
ROUTES D'ANALYSE

Endpoints :
- POST /analysis/validate         → Validation de la structure du fichier
- POST /analysis/validate-insight → Validation d'un insight personnalisé via OpenAI
- POST /analysis/upload           → Upload + conversion + analyse
- GET  /analysis/                 → Liste des analyses de l'utilisateur
- GET  /analysis/{id}             → Résultats d'une analyse spécifique
- DELETE /analysis/{id}           → Suppression d'une analyse
"""

import os
import json
import threading
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Request
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.database.models import User, Analysis, AnalysisStatus
from app.database.session import get_db
from app.schemas.analysis import UploadResponse, AnalysisResult, AnalysisListItem
from app.services.analysis_service import run_analysis_pipeline


# ============================================================================
# CONFIGURATION
# ============================================================================

router = APIRouter(
    prefix="/analysis",
    tags=["Analysis"]
)

TEMP_DIR = Path("data/temp")
UPLOAD_DIR = Path("data/uploads")
RESULTS_DIR = Path("data/results")
TEMP_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


# ============================================================================
# ROUTE : VALIDATION DE LA STRUCTURE DU FICHIER
# ⚠️ Doit être avant /{analysis_id}
# ============================================================================

@router.post("/validate")
async def validate_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return {
            "valid": False,
            "errors": [f"Format non supporté : {ext}. Utilisez .xlsx, .xls ou .csv"],
            "warnings": [], "sheet_count": 0, "row_count": 0,
            "column_count": 0, "missing_pct": 0.0,
            "file_name": file.filename, "file_size": 0,
        }

    try:
        content = await file.read()
    except Exception as e:
        return {
            "valid": False, "errors": [f"Impossible de lire le fichier : {str(e)}"],
            "warnings": [], "sheet_count": 0, "row_count": 0,
            "column_count": 0, "missing_pct": 0.0,
            "file_name": file.filename, "file_size": 0,
        }

    if len(content) > MAX_FILE_SIZE:
        return {
            "valid": False,
            "errors": [f"Fichier trop volumineux. Maximum : {MAX_FILE_SIZE // 1024 // 1024} MB"],
            "warnings": [], "sheet_count": 0, "row_count": 0,
            "column_count": 0, "missing_pct": 0.0,
            "file_name": file.filename, "file_size": len(content),
        }

    temp_path = TEMP_DIR / f"validate_{current_user.id}_{file.filename}"
    with open(temp_path, "wb") as f_out:
        f_out.write(content)

    try:
        from app.services.excel_service import validate_file_structure
        result = validate_file_structure(str(temp_path))
        return {**result, "file_name": file.filename, "file_size": len(content)}
    finally:
        try:
            import gc
            gc.collect()
            if temp_path.exists():
                temp_path.unlink()
        except PermissionError:
            pass


# ============================================================================
# ROUTE : VALIDATION D'UN INSIGHT PERSONNALISÉ VIA OPENAI
# ⚠️ Doit être avant /{analysis_id}
# ============================================================================

@router.post("/validate-insight")
async def validate_custom_insight(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    **Validation d'un insight personnalisé via OpenAI**

    Vérifie si la demande de l'utilisateur est réalisable avec les colonnes
    du dataset, reformule si nécessaire.

    Body : { "description": "texte libre", "analysis_id": 42 }
    """
    body = await request.json()
    description = body.get("description", "").strip()
    analysis_id = body.get("analysis_id")

    if not description:
        return {"valid": False, "reason": "Description vide.", "error": None}

    if len(description) < 5:
        return {"valid": False, "reason": "Description trop courte. Soyez plus précis.", "error": None}

    # Récupération des colonnes depuis l'analyse
    columns = []
    column_types = {}

    if analysis_id:
        analysis = db.query(Analysis).filter(
            Analysis.id == analysis_id,
            Analysis.user_id == current_user.id
        ).first()
        if analysis and analysis.results:
            metadata = analysis.results.get("metadata", {})
            columns = metadata.get("columns", [])
            raw_types = metadata.get("column_types", {})
            column_types = {col: info.get("semantic_type", "text") for col, info in raw_types.items()}

    # Fallback : dernière analyse complète
    if not columns:
        latest = db.query(Analysis).filter(
            Analysis.user_id == current_user.id,
            Analysis.status == AnalysisStatus.COMPLETED
        ).order_by(Analysis.created_at.desc()).first()

        if latest and latest.results:
            metadata = latest.results.get("metadata", {})
            columns = metadata.get("columns", [])
            raw_types = metadata.get("column_types", {})
            column_types = {col: info.get("semantic_type", "text") for col, info in raw_types.items()}

    if not columns:
        return {
            "valid": False,
            "reason": "Impossible de récupérer les colonnes du dataset.",
            "error": None
        }

    from app.services.openai_service import validate_custom_insight as ai_validate
    return ai_validate(description, columns, column_types)


# ============================================================================
# ROUTE : LISTE DES ANALYSES
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
            id=a.id,
            file_name=a.file_name,
            status=a.status.value,
            tokens_consumed=a.tokens_consumed,
            created_at=a.created_at.isoformat() if a.created_at else None
        )
        for a in analyses
    ]


# ============================================================================
# ROUTE : UPLOAD ET ANALYSE
# ============================================================================

@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_and_analyze(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Format non supporté : {ext}. Utilisez .xlsx, .xls ou .csv"
        )

    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la lecture du fichier : {str(e)}"
        )

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Fichier trop volumineux. Maximum : {MAX_FILE_SIZE // 1024 // 1024} MB"
        )

    original_stem = Path(file.filename).stem
    excel_path = TEMP_DIR / file.filename
    json_path = UPLOAD_DIR / f"{original_stem}.json"

    with open(excel_path, "wb") as f_out:
        f_out.write(content)

    try:
        from app.services.excel_service import process_uploaded_file
        processed = process_uploaded_file(str(excel_path), json_path)

        analysis = Analysis(
            user_id=current_user.id,
            file_name=file.filename,
            file_size=len(content),
            file_path=str(json_path),
            status=AnalysisStatus.PROCESSING,
            tokens_consumed=0,
        )
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

        thread = threading.Thread(
            target=run_analysis_background,
            kwargs={
                "analysis_id": analysis.id,
                "dataset_id": original_stem,
                "sample": processed["sample"],
                "metadata": processed["metadata"],
            },
            daemon=True
        )
        thread.start()
        print(f"🚀 Thread lancé pour analyse #{analysis.id}")

        return UploadResponse(
            analysis_id=analysis.id,
            dataset_id=original_stem,
            file_name=file.filename,
            status="processing",
            metadata=processed["metadata"]
        )

    except ValueError as e:
        try:
            if json_path.exists():
                json_path.unlink()
        except Exception:
            pass
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    except Exception as e:
        try:
            if json_path.exists():
                json_path.unlink()
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la conversion : {str(e)}"
        )

    finally:
        try:
            import gc
            gc.collect()
            if excel_path.exists():
                excel_path.unlink()
        except PermissionError:
            pass


# ============================================================================
# TÂCHE DE FOND : PIPELINE IA
# ============================================================================

def run_analysis_background(
    analysis_id: int,
    dataset_id: str,
    sample: dict,
    metadata: dict,
):
    print(f"🔄 Début analyse background #{analysis_id}")

    from app.database.session import SessionLocal
    from app.services.user_service import deduct_tokens
    db = SessionLocal()
    analysis = None
    try:
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if not analysis:
            print(f"❌ Analyse #{analysis_id} introuvable en DB")
            return

        analysis.started_at = datetime.utcnow()
        db.commit()

        print(f"📊 Appel OpenAI pour dataset: {dataset_id}")
        results = run_analysis_pipeline(dataset_id, sample, metadata)
        print(f"✅ Pipeline terminé #{analysis_id}")

        # Calcul du coût réel basé sur la taille du fichier
        file_size_bytes = analysis.file_size or 0
        size_ko = file_size_bytes / 1024
        token_cost = 1 if size_ko <= 10 else int(
            ((-(-size_ko // 10)) * 10) / 10  # arrondi supérieur à la dizaine / 10
        )

        # Déduction des tokens
        user = db.query(User).filter(User.id == analysis.user_id).first()
        if user and user.token_balance >= token_cost:
            deduct_tokens(db, user, token_cost)
            print(f"💰 {token_cost} jeton(s) déduit(s) pour l'utilisateur #{user.id}")
        else:
            print(f"⚠️ Solde insuffisant pour déduire {token_cost} jeton(s)")

        results_path = RESULTS_DIR / f"{dataset_id}_results.json"
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)

        analysis.status = AnalysisStatus.COMPLETED
        analysis.results = results
        analysis.tokens_consumed = token_cost
        analysis.finished_at = datetime.utcnow()
        db.commit()
        print(f"💾 Résultats sauvegardés pour #{analysis_id}")

    except Exception as e:
        print(f"❌ Erreur analyse #{analysis_id}: {e}")
        import traceback
        traceback.print_exc()
        if analysis:
            analysis.status = AnalysisStatus.FAILED
            analysis.error_message = str(e)
            analysis.finished_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


# ============================================================================
# ROUTE : POSER UNE QUESTION SUR UNE ANALYSE VIA OPENAI
# À ajouter dans app/routers/analysis.py avant GET /{analysis_id}
# ============================================================================

@router.post("/{analysis_id}/ask")
async def ask_question(
    analysis_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Pose une question à OpenAI sur une analyse spécifique.
    Retourne service_unavailable=True si OpenAI est indisponible.
    """
    body = await request.json()
    question = body.get("question", "").strip()

    if not question:
        raise HTTPException(status_code=400, detail="Question vide.")

    # Récupération de l'analyse
    analysis = db.query(Analysis).filter(
        Analysis.id == analysis_id,
        Analysis.user_id == current_user.id
    ).first()

    if not analysis:
        raise HTTPException(status_code=404, detail="Analyse introuvable.")

    if analysis.status != AnalysisStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="L'analyse n'est pas encore terminée.")

    # Contexte de l'analyse
    results = analysis.results or {}
    metadata = results.get("metadata", {})
    ai_instructions = results.get("ai_instructions", {})
    insights = results.get("insights", [])

    context = f"""Tu es un assistant expert en analyse de données.
Tu réponds à des questions sur l'analyse suivante :

Fichier : {analysis.file_name}
Lignes : {metadata.get('row_count', 'N/A')}
Colonnes : {metadata.get('columns', [])}
Colonnes numériques : {metadata.get('numeric_columns', [])}
Colonnes catégorielles : {metadata.get('categorical_columns', [])}

Résumé de l'analyse : {ai_instructions.get('summary', 'Non disponible')}

Insights calculés :
{chr(10).join([f"- {i.get('title', '')}: {i.get('value', '')}" for i in insights[:5]])}

Réponds en français, de manière claire et concise. Si la question ne concerne pas ces données, dis-le poliment."""

    try:
        from app.services.openai_service import get_openai_client
        client = get_openai_client()

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": context},
                {"role": "user", "content": question}
            ],
            temperature=0.3,
            max_tokens=800,
        )

        answer = response.choices[0].message.content.strip()
        return {"answer": answer, "service_unavailable": False}

    except Exception as e:
        error_msg = str(e)

        if "quota" in error_msg.lower() or "429" in error_msg:
            detail = "Quota OpenAI épuisé. Le service d'analyse IA est temporairement indisponible."
        elif "api_key" in error_msg.lower() or "authentication" in error_msg.lower():
            detail = "Service d'analyse IA indisponible. Veuillez réessayer plus tard."
        elif "connection" in error_msg.lower():
            detail = "Impossible de contacter le service IA. Vérifiez votre connexion internet."
        else:
            detail = "Service d'analyse IA temporairement indisponible."

        return {
            "answer": None,
            "service_unavailable": True,
            "detail": detail
        }


# ============================================================================
# ROUTE : RÉSULTATS D'UNE ANALYSE
# ⚠️ Doit être après /validate, /validate-insight et /upload
# ============================================================================

@router.get("/{analysis_id}", response_model=AnalysisResult)
async def get_analysis(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()

    if not analysis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analyse introuvable")

    if analysis.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès refusé")

    results = analysis.results or {}

    return AnalysisResult(
        analysis_id=analysis.id,
        dataset_id=str(analysis.file_path).split("/")[-1].replace(".json", "") if analysis.file_path else "",
        file_name=analysis.file_name,
        status=analysis.status.value,
        metadata=results.get("metadata"),
        charts=results.get("charts"),
        insights=results.get("insights"),
        explanations=results.get("explanations"),
        ai_instructions=results.get("ai_instructions"),
        created_at=analysis.created_at.isoformat() if analysis.created_at else None
    )


# ============================================================================
# ROUTE : SUPPRESSION D'UNE ANALYSE
# ============================================================================

@router.delete("/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_analysis(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()

    if not analysis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analyse introuvable")

    if analysis.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès refusé")

    if analysis.file_path:
        json_path = Path(analysis.file_path)
        if json_path.exists():
            json_path.unlink()

    db.delete(analysis)
    db.commit()