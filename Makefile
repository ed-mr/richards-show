# CI / deployment for fb-slideshow.
#
#   make test          tests on this machine (itaka), with the same Python and
#                      library versions as on hydra (requirements-ci.txt)
#   make deploy        copy the program to hydra (scp) and install/update the
#                      systemd user service (starts the show at boot)
#   make test-hydra    run the tests on hydra, check that versions match and
#                      verify the service file
#   make start|stop|restart|pause|resume|status
#                      control the show on hydra (ssh -> systemctl --user)
#   make config SHOW_ARGS="--shuffle --hold 20"
#                      set the show's options (kept across reboots and
#                      deploys); SHOW_ARGS= resets to defaults
#   make ci            test + deploy + test-hydra + restart (what CI runs)

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

.PHONY: test deploy test-hydra start stop restart pause resume status config ci clean

$(VENV)/.stamp: requirements-ci.txt
	$(UV) venv -q --clear --python $(PYTHON_VERSION) $(VENV)
	$(UV) pip install -q --python $(VENV)/bin/python -r requirements-ci.txt
	touch $@

test: $(VENV)/.stamp
	$(VENV)/bin/python -m pytest -q -p no:cacheprovider

deploy:
	$(SSH) $(HYDRA) 'mkdir -p $(DEPLOY_DIR)/ci'
	$(SCP) $(DEPLOY_FILES) $(HYDRA):$(DEPLOY_DIR)/
	$(SCP) ci/show.sh ci/fb-slideshow.service $(HYDRA):$(DEPLOY_DIR)/ci/
	$(SSH) $(HYDRA) 'chmod +x $(DEPLOY_DIR)/ci/show.sh && $(DEPLOY_DIR)/ci/show.sh install'
	@echo "deployed to $(HYDRA):$(DEPLOY_DIR)"

test-hydra: $(VENV)/.stamp
	@local="$$($(VENV)/bin/python -c '$(VERSIONS)')"; \
	remote="$$($(SSH) $(HYDRA) "python3 -c '$(VERSIONS)'")"; \
	echo "itaka: $$local"; echo "hydra: $$remote"; \
	if [ "$$local" != "$$remote" ]; then \
		echo "version mismatch: update requirements-ci.txt / PYTHON_VERSION" >&2; exit 1; fi
	$(SSH) $(HYDRA) 'cd $(DEPLOY_DIR) && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider'
	$(SSH) $(HYDRA) 'systemd-analyze --user verify ~/.config/systemd/user/fb-slideshow.service && echo "service file ok"'

start stop restart pause resume status:
	@$(SSH) $(HYDRA) '$(DEPLOY_DIR)/ci/show.sh $@'

config:
	@$(SSH) $(HYDRA) "$(DEPLOY_DIR)/ci/show.sh config '$(SHOW_ARGS)'"

ci: test deploy test-hydra restart

clean:
	rm -rf $(VENV)
