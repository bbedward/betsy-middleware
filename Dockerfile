FROM python:3.7

WORKDIR /usr/src/app

COPY . ./
RUN pip install --trusted-host pypi.org --no-cache-dir -r requirements.txt

CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "5555"]