from fastapi import Request


def get_dynamodb_client(request: Request):
    """FastAPI dependency — returns the aioboto3 DynamoDB low-level client
    stored on app.state during lifespan startup."""
    return request.app.state.dynamodb_client
