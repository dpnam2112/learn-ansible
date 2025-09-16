from fastapi import FastAPI
import os, psycopg2

app = FastAPI()

def conn():
    # TODO
    ...

@app.get("/healthz")
def health():
    return {"status":"ok"}
