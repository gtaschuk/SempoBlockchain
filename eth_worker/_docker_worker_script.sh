#!/usr/bin/env bash
sleep 10

if [ "$CONTAINER_TYPE" == 'BEAT' ]; then
  celery -A eth_manager beat --loglevel=WARNING
elif [ "$CONTAINER_TYPE" == 'FILTER' ]; then
  python ethereum_filter_test.py
elif [ "$CONTAINER_TYPE" == 'PROCESSOR' ]; then
    if [ "$CONTAINER_MODE" = 'TEST' ]; then
        celery -A eth_manager worker --loglevel=INFO --concurrency=4 --pool=eventlet -Q=processor
    else
        celery -A eth_manager worker --loglevel=INFO --concurrency=4 --pool=eventlet -Q=processor
    fi
else
  alembic upgrade head

  ret=$?
  if [ "$ret" -ne 0 ]; then
    exit $ret
  fi

    if [ "$CONTAINER_MODE" = 'TEST' ]; then
        coverage run invoke_celery.py
    else
        celery -A eth_manager worker --loglevel=INFO --concurrency=10 --pool=eventlet
    fi

fi

#
#celery -A worker beat --loglevel=WARNING
