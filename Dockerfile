# Указываем Python 3.12
FROM python:3.12-slim

# Рабочая папка
WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

# Обновляем pip и устанавливаем зависимости
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Копируем весь проект
COPY . .

# Команда запуска (замени bot.py на свой файл)
CMD ["python", "bot.py"]
