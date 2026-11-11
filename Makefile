# CI / deployment for fb-slideshow.
#
#   make test          tests on this machine (itaka), with the same Python and
#                      library versions as on hydra (requirements-ci.txt)
#   make deploy        copy the program to hydra (scp)
#   make test-hydra    run the tests on hydra and check that versions match
#   make start|stop|restart|pause|resume|status
#                      control the show on hydra (ssh)
#   make ci            test + deploy + test-hydra + restart (what CI runs)
#
# Override on the command line, e.g.  make start SHOW_ARGS="--shuffle --hold 20"

HYDRA      ?= hydra
DEPLOY_DIR ?= fb-slideshow
SHOW_ARGS  ?=
PYTHON_VERSION ?= 3.13.5

UV   ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
VENV := .venv-ci
SSH  := ssh -o BatchMode=yes -o ConnectTimeout=10
SCP  := scp -q -o BatchMode=yes -o ConnectTimeout=10

DEPLOY_FILES := fb-slideshow.py test_fb_slideshow.py requirements.txt
VERSIONS := import sys, numpy, PIL; print(sys.version.split()[0], numpy.__version__, PIL.__version__)

.PHONY: test deploy test-hydra start stop restart pause resume status ci clean

$(VENV)/.stamp: requirements-ci.txt
	$(UV) venv -q --clear --python $(PYTHON_VERSION) $(VENV)
	$(UV) pip install -q --python $(VENV)/bin/python -r requirements-ci.txt
	touch $@

test: $(VENV)/.stamp
	$(VENV)/bin/python -m pytest -q -p no:cacheprovider

deploy:
	$(SSH) $(HYDRA) 'mkdir -p $(DEPLOY_DIR)/ci'
	$(SCP) $(DEPLOY_FILES) $(HYDRA):$(DEPLOY_DIR)/
	$(SCP) ci/show.sh $(HYDRA):$(DEPLOY_DIR)/ci/
	$(SSH) $(HYDRA) 'chmod +x $(DEPLOY_DIR)/ci/show.sh'
	@echo "deployed to $(HYDRA):$(DEPLOY_DIR)"

test-hydra: $(VENV)/.stamp
	@local="$$($(VENV)/bin/python -c '$(VERSIONS)')"; \
	remote="$$($(SSH) $(HYDRA) "python3 -c '$(VERSIONS)'")"; \
	echo "itaka: $$local"; echo "hydra: $$remote"; \
	if [ "$$local" != "$$remote" ]; then \
		echo "version mismatch: update requirements-ci.txt / PYTHON_VERSION" >&2; exit 1; fi
	$(SSH) $(HYDRA) 'cd $(DEPLOY_DIR) && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider'

start stop restart pause resume status:
	@$(SSH) $(HYDRA) "SHOW_ARGS='$(SHOW_ARGS)' $(DEPLOY_DIR)/ci/show.sh $@"

ci: test deploy test-hydra restart

clean:
	rm -rf $(VENV)
