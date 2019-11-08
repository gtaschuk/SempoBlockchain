#!/usr/bin/env bash
sleep 10
alembic upgrade head
if [ "$CONTAINER_MODE" = 'TEST' ]; then
  echo pass
elif [ "$CONTAINER_TYPE" == 'BEAT' ]; then
  celery -A worker beat --loglevel=WARNING
elif [ "$CONTAINER_TYPE" == 'FILTER' ]; then
  python ethereum_filter_test.py
else
  celery -A eth_manager worker --loglevel=INFO --concurrency=500 --pool=eventlet
fi

#
#celery -A worker beat --loglevel=WARNING