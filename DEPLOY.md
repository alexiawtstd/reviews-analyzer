# Инструкция по развертыванию

## Быстрый старт на Render.com

### Шаг 1: Подготовка репозитория GitHub

1. Создайте новый репозиторий на GitHub
2. Загрузите код в репозиторий:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/ВАШ_USERNAME/ВАШ_РЕПОЗИТОРИЙ.git
git push -u origin main
```

### Шаг 2: Регистрация на Render

1. Перейдите на https://render.com
2. Зарегистрируйтесь через GitHub (удобнее всего)

### Шаг 3: Создание Web Service

1. Нажмите "New +" → "Web Service"
2. Выберите "Connect GitHub" и авторизуйтесь
3. Выберите ваш репозиторий
4. Нажмите "Connect"

### Шаг 4: Настройка сервиса

Заполните форму:

- **Name**: `reviews-analyzer` (или любое другое имя)
- **Region**: выберите ближайший (например, Frankfurt)
- **Branch**: `main` (или ваша основная ветка)
- **Root Directory**: оставьте пустым
- **Environment**: `Python 3`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn app:app`

### Шаг 5: Переменные окружения

В разделе "Environment Variables" добавьте:

- **Key**: `SECRET_KEY`
- **Value**: сгенерируйте случайный ключ:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

Или используйте онлайн-генератор: https://randomkeygen.com/

### Шаг 6: План

- Выберите **Free** план (для начала)

### Шаг 7: Создание

Нажмите "Create Web Service"

### Шаг 8: Ожидание деплоя

- Первый деплой займет **10-20 минут** из-за загрузки модели ML
- Следите за логами в разделе "Logs"
- После успешного деплоя вы получите URL вида: `https://reviews-analyzer.onrender.com`

## Альтернативные платформы

### Railway.app

1. Зайдите на https://railway.app
2. "New Project" → "Deploy from GitHub repo"
3. Выберите репозиторий
4. Добавьте переменную `SECRET_KEY`
5. Готово! Railway автоматически определит настройки

### Heroku

```bash
# Установите Heroku CLI
heroku login
heroku create your-app-name
heroku config:set SECRET_KEY=your-secret-key
git push heroku main
heroku open
```

## Проверка работы

После деплоя:

1. Откройте предоставленный URL
2. Зарегистрируйтесь
3. Попробуйте проанализировать отзыв

## Решение проблем

### Ошибка: "Application failed to respond"

- Проверьте логи в разделе "Logs"
- Убедитесь, что `SECRET_KEY` установлен
- Проверьте, что `gunicorn` установлен (есть в requirements.txt)

### Ошибка: "Out of memory"

- Модель ML требует много памяти
- На бесплатном тарифе Render может не хватить памяти
- Попробуйте Railway или обновите план

### Медленная загрузка

- Первый запуск всегда медленный (загрузка модели)
- Последующие запросы будут быстрее

## Обновление приложения

После изменений в коде:

```bash
git add .
git commit -m "Update"
git push
```

Платформа автоматически пересоберет и перезапустит приложение.

