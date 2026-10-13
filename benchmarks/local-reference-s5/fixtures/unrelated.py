from fastapi import FastAPI  # noqa: F401 - deliberate unused import hard negative


class Unrelated:
    def get(self, path):
        return lambda fn: fn


app = Unrelated()


@app.get("/shadow/{id}")
def shadow(id: str):
    pass
