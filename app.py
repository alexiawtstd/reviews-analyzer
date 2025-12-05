from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import cloudscraper
from bs4 import BeautifulSoup
from transformers import pipeline
import re
import os
from dotenv import load_dotenv
import time
import random
import logging
import torch

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'my-secret-key-123')

# бд
database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///reviews_analyzer.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Пожалуйста, войдите для доступа к этой странице.'
login_manager.login_message_category = 'error'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    analyses = db.relationship('Analysis', backref='user', lazy=True)

class Analysis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_url = db.Column(db.String(500), nullable=False)
    product_name = db.Column(db.String(200))
    positive_percent = db.Column(db.Float)
    neutral_percent = db.Column(db.Float)
    negative_percent = db.Column(db.Float)
    overall_rating = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# загрузка модели
print("Загрузка модели...")
try:
    sentiment_analyzer = pipeline(
        "sentiment-analysis",
        model="seara/rubert-tiny2-russian-sentiment",
        tokenizer="seara/rubert-tiny2-russian-sentiment"
    )
    print("Модель загружена успешно!")
except Exception as e:
    print(f"Ошибка загрузки модели: {e}")
    sentiment_analyzer = None

# обход защиты сайта, с которого парсим
def create_fresh_scraper():
    s = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )
    s.headers.update({
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    return s

scraper = create_fresh_scraper()

def get_html_content(url):
    global scraper
    retries = 3

    for i in range(retries):
        try:
            time.sleep(random.uniform(2, 5))
            r = scraper.get(url)
            if r.status_code == 200:
                return r.text

            if r.status_code in [403, 521, 520]:
                print(f"Попытка {i + 1}/{retries}: Защита {r.status_code}. Меняю сессию...")
                scraper = create_fresh_scraper()
                time.sleep(5)
                continue

            print(f"Ошибка доступа к {url}: {r.status_code}")
            return None

        except Exception as e:
            print(f"Ошибка сети при запросе {url}: {e}")
            time.sleep(3)

    print("Не удалось пробиться через защиту после всех попыток.")
    return None

# парсинг
def parse_reviews(url_tovara):
    base_url = 'https://irecommend.ru'
    print(f"Начинаю парсинг: {url_tovara}")
    html = get_html_content(url_tovara)
    if not html:
        return "Ошибка доступа", []
    soup = BeautifulSoup(html, 'html.parser')

    title_tag = soup.find('h1')
    product_name = title_tag.get_text(strip=True) if title_tag else "Неизвестный товар"

    links_to_parse = []
    reviews_container = soup.find('ul', class_='list-comments')

    if reviews_container:
        all_links = reviews_container.find_all('a')
        for link in all_links:
            href = link.get('href')
            if href and href.startswith('/content/'):
                full_link = base_url + href
                product_path = url_tovara.replace(base_url, '')

                if href != product_path and full_link not in links_to_parse:
                    links_to_parse.append(full_link)
    else:
        print("!!! Не нашел контейнер <ul class='list-comments'>. Возможно, сработала защита.")

    final_links = links_to_parse[:20]
    print(f"Найдено ссылок: {len(links_to_parse)}. Буду обрабатывать: {len(final_links)}")

    reviews_text_list = []

    count = 0
    for link in final_links:
        count += 1
        print(f"[{count}] Качаю отзыв: {link}")

        review_html = get_html_content(link)
        if review_html:
            soup_rev = BeautifulSoup(review_html, 'html.parser')

            review_body = soup_rev.find('div', itemprop='reviewBody')

            if not review_body:
                review_body = soup_rev.find('div', class_='description')

            if review_body:
                text = review_body.get_text(separator=' ', strip=True)
                if len(text) > 50:
                    reviews_text_list.append(text)
            else:
                print("    Текст не найден внутри страницы")
        else:
            print("    Не удалось открыть страницу отзыва")

    return product_name, reviews_text_list

# анализ отзывов с помощью модели
def analyze_sentiment(reviews):
    if not reviews:
        return 0, 0, 0, 0

    positive = 0
    neutral = 0
    negative = 0

    for review in reviews:
        try:
            review_text = review
            result = sentiment_analyzer(review_text)

            label = result[0]['label']

            if label == 'positive':
                positive += 1
            elif label == 'negative':
                negative += 1
            else:
                neutral += 1

        except Exception as e:
            print(f"Ошибка анализа одного отзыва: {e}")
            neutral += 1

    total = len(reviews)
    if total == 0: return 0, 0, 0, 0

    pos_pct = (positive / total) * 100
    neu_pct = (neutral / total) * 100
    neg_pct = (negative / total) * 100

    score_sum = (positive * 5) + (neutral * 3) + (negative * 1)
    overall = score_sum / total

    return pos_pct, neu_pct, neg_pct, overall

# связь с фронтендом
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('instruction'))
        else:
            flash('Неверное имя пользователя или пароль', 'error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')

        if User.query.filter_by(username=username).first():
            flash('Имя занято', 'error')
            return render_template('login.html', register=True)
        if User.query.filter_by(email=email).first():
            flash('Email занят', 'error')
            return render_template('login.html', register=True)

        user = User(username=username, email=email, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for('instruction'))
    return render_template('login.html', register=True)

@app.route('/instruction')
@login_required
def instruction():
    return render_template('instruction.html')

@app.route('/analyze', methods=['POST'])
@login_required
def analyze():
    url = request.form.get('url')

# то, что разбивает нам сердце (обработка ошибок)
    if not url or 'irecommend' not in url:
        flash('Введите корректную ссылку на irecommend.ru', 'error')
        return redirect(url_for('instruction'))

    product_name, reviews = parse_reviews(url)

    if not reviews:
        flash('Не удалось найти отзывы. Возможно, защита сайта не пускает или отзывов нет:(', 'error')
        return redirect(url_for('instruction'))

    if sentiment_analyzer is None:
        flash('Нейросеть не загружена. Проверьте консоль.', 'error')
        return redirect(url_for('instruction'))

# подгрузка анализа в историю
    pos, neu, neg, rating = analyze_sentiment(reviews)

    analysis = Analysis(
        user_id=current_user.id,
        product_url=url,
        product_name=product_name,
        positive_percent=pos,
        neutral_percent=neu,
        negative_percent=neg,
        overall_rating=rating
    )
    db.session.add(analysis)
    db.session.commit()

    return render_template('results.html',
                           product_name=product_name,
                           positive=round(pos, 1),
                           neutral=round(neu, 1),
                           negative=round(neg, 1),
                           overall_rating=round(rating, 2),
                           reviews_count=len(reviews))

@app.route('/profile')
@login_required
def profile():
    analyses = Analysis.query.filter_by(user_id=current_user.id).order_by(Analysis.created_at.desc()).all()
    return render_template('profile.html', user=current_user, analyses=analyses)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
