FROM python:3.9-alpine as build

WORKDIR /build

COPY / ./

RUN apk update && \
    apk add --no-cache git gcc build-base libffi-dev openssl-dev python3-dev

RUN pip wheel -w pkg -r requirements.txt .

FROM python:3.9-alpine

WORKDIR /app

COPY --from=build /build/pkg /app/pkg

RUN pip install -f /app/pkg wemo-timer && \
    rm -rf /app/pkg

COPY settings.toml /app/settings.toml

EXPOSE 8080

CMD [ "wemo-timer" ]
