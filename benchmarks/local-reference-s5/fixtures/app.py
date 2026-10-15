from fastapi import FastAPI

app = FastAPI()


@app.get("/customers/{id}")
def customer(id: str):
    pass


@app.get("/orders")
def orders_similar_name():
    pass
