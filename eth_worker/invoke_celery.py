from eth_manager import celery_app


if __name__ == '__main__':
    celery_app.start(argv=['eth_manager', 'worker', '-l', 'info', '--pool', 'eventlet', '--concurrency', '10'])

    # app = current_app._get_current_object()
    #
    # worker = worker.worker(app=app)
    #
    #
    # worker.run(**options)