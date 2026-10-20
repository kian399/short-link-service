from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import settings
from app.exceptions import Unauthorized


def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key != settings.API_KEY:
        raise Unauthorized()
    return x_api_key
