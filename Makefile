FLAKE8 = $(shell which flake8.3 flake8 | head -n1)

.PHONY: all reinstall uninstall-y install has-virtualenv

all: flake8

reload:
	pkill -P1 -HUP -uplanb uwsgi
	systemctl restart planb-queue.service 

reinstall: uninstall-y install

uninstall-y: has-virtualenv
	pip uninstall -y planb
	
install: has-virtualenv
	pip install .

has-virtualenv:
	@test -n "$$VIRTUAL_ENV"

.PHONY: flake8

flake8:
	test -n "$(FLAKE8)"
	find . -type f -name '*.py' '!' -path '*/migrations/*' \
	  '!' -path './.*' | LC_ALL=C sort | xargs -d'\n' $(FLAKE8)
