def post_worker_init(worker):
    from app import start_history_updates
    start_history_updates()
