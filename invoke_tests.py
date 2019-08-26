import pytest
import os, sys

if os.environ.get("LOCATION") != 'LOCAL_DOCKER':
    os.environ['DEPLOYMENT_NAME'] = "TEST"
    os.environ['LOCATION'] = "TEST"

if __name__ == '__main__':
    # use '--setup-show' to show fixture SETUP and TEARDOWN
    # -k "MyClass and not method" will run TestMyClass.test_something but not TestMyClass.test_method_simple.
    r = pytest.main(['-v', '-s', '-x', 'test'] + sys.argv[1:])
    exit(r)