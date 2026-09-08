from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.schemas.user import UserProfile, UserUpdate, ChangePasswordRequest
from app.dependencies.auth import get_current_user
from app.database.models import User
from app.services.user_service import update_user_profile, change_user_password

router = APIRouter(
    tags=["User"]
)

@router.put("/user/profile", response_model=UserProfile)
async def update_profile(
    request: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not request.first_name or not request.last_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le prénom et le nom sont requis."
        )

    updated_user = update_user_profile(
        db=db,
        user=current_user,
        first_name=request.first_name,
        last_name=request.last_name
    )
    return UserProfile.model_validate(updated_user)


@router.post("/user/change-password", status_code=status.HTTP_200_OK)
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    success = change_user_password(
        db=db,
        user=current_user,
        current_password=request.currentPassword,
        new_password=request.newPassword
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le mot de passe actuel est incorrect."
        )

    return {"message": "Mot de passe mis à jour avec succès."}