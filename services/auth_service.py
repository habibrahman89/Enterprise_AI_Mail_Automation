from werkzeug.security import check_password_hash
def verify_password(password,password_hash): return check_password_hash(password_hash,password)
