# setup project
FROM python:3.12-slim
WORKDIR /passiton/

# Set python binaries
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# install dependencies
COPY requirements.txt /passiton/
RUN pip install -r requirements.txt --no-cache-dir

# copy project files
COPY . /passiton/

# run test
RUN python -m coverage run --rcfile=tests/.coveragerc manage.py test tests --settings=tests.settings
RUN python -m coverage report --rcfile=tests/.coveragerc

# Expose port
EXPOSE 8000

# run on entrance
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
