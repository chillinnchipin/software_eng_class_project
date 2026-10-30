# Setup project
FROM python:3.12-slim
ENV APP_DIR=/home/passiton/passiton-books/
WORKDIR ${APP_DIR}
ENV CONTAINER_RUNTIME=1

# Set python binaries
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Setup developer user
RUN apt-get update
RUN apt-get install sudo -y
RUN useradd -m passiton && echo "passiton:passiton" | chpasswd && adduser passiton sudo
RUN echo "passiton ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers
RUN chown -R passiton:passiton ${APP_DIR}
USER passiton

# Install dependencies
COPY --chown=passiton:passiton scripts/ ${APP_DIR}/scripts/
COPY --chown=passiton:passiton requirements.txt ${APP_DIR}
RUN ${APP_DIR}/scripts/bash/install_dependencies.sh ${APP_DIR}

# Copy project files
COPY --chown=passiton:passiton manage.py ${APP_DIR}
COPY --chown=passiton:passiton fixtures/ ${APP_DIR}/fixtures/
COPY --chown=passiton:passiton tests/ ${APP_DIR}/tests/
COPY --chown=passiton:passiton config/ ${APP_DIR}/config/
#COPY static/ ${APP_DIR}/static/
COPY --chown=passiton:passiton app/ ${APP_DIR}/app/

# Run tests
RUN COVERAGE_FILE=/tmp/passiton-coverage ${APP_DIR}/scripts/bash/run_tests.sh ${APP_DIR}

# Expose port
EXPOSE 8000

# Run app
ENTRYPOINT ["/bin/sh", "-c", "exec \"$APP_DIR/scripts/bash/start_app.sh\" \"$APP_DIR\""]
