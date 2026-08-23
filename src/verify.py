
def verify_password(password, stored_hash):
    # Vulnerability: plain text comparison
    if password == stored_hash:
        return True
    return False

