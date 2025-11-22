from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from transformers import pipeline
import re
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///reviews_analyzer.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Модель пользователя
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    analyses = db.relationship('Analysis', backref='user', lazy=True)

# Модель анализа
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

# Инициализация модели анализа тональности
print("Загрузка модели анализа тональности...")
try:
    sentiment_analyzer = pipeline(
        "sentiment-analysis",
        model="blanchefort/rubert-base-sentiment",
        tokenizer="blanchefort/rubert-base-sentiment"
    )
    print("Модель загружена успешно!")
except Exception as e:
    print(f"Ошибка загрузки модели: {e}")
    sentiment_analyzer = None

def parse_reviews(url):
    """Парсит отзывы с сайта irecommend.com"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Извлекаем название товара
        product_name = "Неизвестный товар"
        title_tag = soup.find('h1')
        if title_tag:
            product_name = title_tag.get_text(strip=True)
        
        # Ищем отзывы - пробуем различные варианты структуры сайта irecommend.com
        reviews = []
        
        # Вариант 1: Поиск по itemprop (стандартная разметка)
        review_elements = soup.find_all('div', {'itemprop': 'reviewBody'})
        
        # Вариант 2: Поиск по классам содержащим "review", "comment", "text"
        if not review_elements:
            review_elements = soup.find_all('div', class_=re.compile(r'review|comment|text', re.I))
        
        # Вариант 3: Поиск по классам с "review" в названии
        if not review_elements:
            review_elements = soup.find_all('div', class_=lambda x: x and 'review' in x.lower())
        
        # Вариант 4: Поиск по структуре irecommend.com (блоки с отзывами)
        if not review_elements:
            # Ищем блоки с классом "reviewText" или похожими
            review_elements = soup.find_all(['div', 'p'], class_=lambda x: x and any(
                word in x.lower() for word in ['review', 'comment', 'text', 'content', 'description']
            ))
        
        # Извлекаем текст отзывов
        for element in review_elements[:30]:  # Берем больше для фильтрации
            text = element.get_text(strip=True)
            # Фильтруем: минимум 30 символов, максимум 5000, убираем дубликаты
            if text and 30 < len(text) < 5000 and text not in reviews:
                # Убираем служебные тексты
                if not any(skip in text.lower() for skip in ['читать далее', 'показать полностью', 'скрыть']):
                    reviews.append(text)
                    if len(reviews) >= 20:
                        break
        
        # Если не нашли отзывы, пробуем найти все параграфы с достаточным текстом
        if len(reviews) < 5:
            paragraphs = soup.find_all('p')
            for p in paragraphs:
                text = p.get_text(strip=True)
                if text and 50 < len(text) < 2000 and text not in reviews:
                    reviews.append(text)
                    if len(reviews) >= 20:
                        break
        
        return product_name, reviews[:20]
    
    except Exception as e:
        print(f"Ошибка при парсинге: {e}")
        return None, []

def analyze_sentiment(reviews):
    """Анализирует тональность отзывов"""
    if not reviews:
        return None, None, None, None
    
    positive = 0
    neutral = 0
    negative = 0
    
    for review in reviews:
        try:
            # Ограничиваем длину для модели (BERT обычно работает с максимум 512 токенами)
            review_text = review[:512] if len(review) > 512 else review
            result = sentiment_analyzer(review_text)
            
            # Модель blanchefort/rubert-base-sentiment возвращает метки на русском
            label = result[0]['label'].lower()
            score = result[0].get('score', 0.5)
            
            # Проверяем различные варианты меток
            if 'positive' in label or 'позитив' in label or 'pos' in label or 'положител' in label:
                positive += 1
            elif 'negative' in label or 'негатив' in label or 'neg' in label or 'отрицател' in label:
                negative += 1
            elif 'neutral' in label or 'нейтрал' in label or 'нейтр' in label:
                neutral += 1
            else:
                # Если метка не распознана, используем score для определения
                if score > 0.6:
                    positive += 1
                elif score < 0.4:
                    negative += 1
                else:
                    neutral += 1
        except Exception as e:
            print(f"Ошибка анализа отзыва: {e}")
            neutral += 1
    
    total = len(reviews)
    positive_percent = (positive / total) * 100 if total > 0 else 0
    neutral_percent = (neutral / total) * 100 if total > 0 else 0
    negative_percent = (negative / total) * 100 if total > 0 else 0
    
    # Общая оценка (от 1 до 5)
    # Формула: 1 (минимум) + процент положительных * 4
    overall_rating = 1 + (positive_percent / 100) * 4
    
    return positive_percent, neutral_percent, negative_percent, overall_rating

@app.route('/')
def index():
    """Главная страница"""
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Страница авторизации"""
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
    """Регистрация"""
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        if User.query.filter_by(username=username).first():
            flash('Пользователь с таким именем уже существует', 'error')
            return render_template('login.html', register=True)
        
        if User.query.filter_by(email=email).first():
            flash('Пользователь с таким email уже существует', 'error')
            return render_template('login.html', register=True)
        
        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password)
        )
        db.session.add(user)
        db.session.commit()
        
        login_user(user)
        return redirect(url_for('instruction'))
    
    return render_template('login.html', register=True)

@app.route('/instruction')
@login_required
def instruction():
    """Страница инструкции"""
    return render_template('instruction.html')

@app.route('/analyze', methods=['POST'])
@login_required
def analyze():
    """Обработка анализа отзывов"""
    url = request.form.get('url')
    
    if not url:
        flash('Пожалуйста, введите ссылку на товар', 'error')
        return redirect(url_for('instruction'))
    
    # Проверяем, что это ссылка на irecommend.com
    if 'irecommend.ru' not in url and 'irecommend.com' not in url:
        flash('Пожалуйста, введите ссылку с сайта irecommend.ru или irecommend.com', 'error')
        return redirect(url_for('instruction'))
    
    # Парсим отзывы
    product_name, reviews = parse_reviews(url)
    
    if not reviews:
        flash('Не удалось найти отзывы. Проверьте ссылку.', 'error')
        return redirect(url_for('instruction'))
    
    # Проверяем, что модель загружена
    if sentiment_analyzer is None:
        flash('Модель анализа тональности не загружена. Пожалуйста, перезапустите сервер.', 'error')
        return redirect(url_for('instruction'))
    
    # Анализируем тональность
    positive, neutral, negative, overall_rating = analyze_sentiment(reviews)
    
    if positive is None:
        flash('Ошибка при анализе отзывов', 'error')
        return redirect(url_for('instruction'))
    
    # Сохраняем анализ в базу данных
    analysis = Analysis(
        user_id=current_user.id,
        product_url=url,
        product_name=product_name,
        positive_percent=positive,
        neutral_percent=neutral,
        negative_percent=negative,
        overall_rating=overall_rating
    )
    db.session.add(analysis)
    db.session.commit()
    
    return render_template('results.html',
                         product_name=product_name,
                         positive=round(positive, 1),
                         neutral=round(neutral, 1),
                         negative=round(negative, 1),
                         overall_rating=round(overall_rating, 2),
                         reviews_count=len(reviews))

@app.route('/profile')
@login_required
def profile():
    """Страница профиля"""
    analyses = Analysis.query.filter_by(user_id=current_user.id).order_by(Analysis.created_at.desc()).all()
    return render_template('profile.html', user=current_user, analyses=analyses)

@app.route('/logout')
@login_required
def logout():
    """Выход из системы"""
    logout_user()
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(debug=debug, host='0.0.0.0', port=port)

