from authentication_utils import create_client_with_user_credentials
from google.apps import chat_v1 as google_chat

SCOPES = ["https://www.googleapis.com/auth/chat.memberships.readonly"]

# This sample shows how to list memberships with user credential
def list_memberships_user_cred():
    # Create a client
    client = create_client_with_user_credentials(SCOPES)

    # Initialize request argument(s)
    request = google_chat.ListMembershipsRequest(
        # Replace SPACE_NAME here
        parent = 'spaces/SPACE_NAME',
        # Filter membership by type (HUMAN or BOT) or role (ROLE_MEMBER or
        # ROLE_MANAGER)
        filter = 'member.type = "HUMAN"',
        # Number of results that will be returned at once
        page_size = 100
    )

    # Make the request
    page_result = client.list_memberships(request)

    # Handle the response. Iterating over page_result will yield results and
    # resolve additional pages automatically.
    for response in page_result:
        print(response)

list_memberships_user_cred()
