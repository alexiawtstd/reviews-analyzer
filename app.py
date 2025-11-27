from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
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
from urllib.parse import urlparse
import time
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///reviews_analyzer.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 час

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Пожалуйста, войдите для доступа к этой странице.'
login_manager.login_message_category = 'error'

# Модель пользователя
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    analyses = db.relationship('Analysis', backref='user', lazy=True, cascade='all, delete-orphan')

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
    reviews_count = db.Column(db.Integer)  # Добавим количество обработанных отзывов
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
        tokenizer="blanchefort/rubert-base-sentiment",
        truncation=True,
        max_length=512
    )
    print("Модель загружена успешно!")
except Exception as e:
    print(f"Ошибка загрузки модели: {e}")
    sentiment_analyzer = None

def is_valid_irecommend_url(url):
    """Проверяет, является ли URL корректной ссылкой на iRecommend"""
    try:
        parsed = urlparse(url)
        return parsed.netloc.endswith(('irecommend.ru', 'irecommend.com')) and parsed.scheme in ('http', 'https')
    except Exception:
        return False

def parse_reviews(url):
    """Парсит отзывы с сайта irecommend.com"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        logger.info(f"Начинаем парсинг URL: {url}")
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Проверяем, что это HTML страница
        if 'text/html' not in response.headers.get('content-type', ''):
            logger.error("Получен не HTML контент")
            return None, []
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Извлекаем название товара
        product_name = "Неизвестный товар"
        title_selectors = [
            'h1',
            '.product-title',
            '.title',
            '[itemprop="name"]',
            '.product-name',
            '.page-title'
        ]
        
        for selector in title_selectors:
            title_tag = soup.select_one(selector)
            if title_tag:
                product_name = title_tag.get_text(strip=True)
                logger.info(f"Найдено название товара: {product_name}")
                break
        
        # Поиск отзывов - специфичные селекторы для iRecommend
        reviews = []
        review_selectors = [
            '.review-text',
            '.reviewText',
            '.review-content',
            '.description',
            '[itemprop="description"]',
            '.item-description',
            '.text'
        ]
        
        for selector in review_selectors:
            review_elements = soup.select(selector)
            logger.info(f"Найдено элементов с селектором {selector}: {len(review_elements)}")
            
            for element in review_elements:
                text = element.get_text(strip=True)
                # Более строгая фильтрация
                if (text and 30 <= len(text) <= 2000 and  # Минимум 30 символов, максимум 2000
                    text not in reviews and
                    len(text.split()) >= 5 and  # Минимум 5 слов
                    not any(skip in text.lower() for skip in [
                        'читать далее', 'показать полностью', 'скрыть',
                        'отзыв полезен', 'комментарий', 'ответить',
                        'рейтинг', 'оценка', 'поделиться', 'пожаловаться'
                    ])):
                    reviews.append(text)
                    logger.debug(f"Добавлен отзыв: {text[:50]}...")
                    if len(reviews) >= 20:
                        break
            if len(reviews) >= 20:
                break
        
        # Если отзывов меньше 20 - это нормально, работаем с тем что есть
        logger.info(f"Всего найдено отзывов: {len(reviews)}")
        
        return product_name, reviews
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при парсинге: {e}")
        return None, []
    except Exception as e:
        logger.error(f"Ошибка при парсинге: {e}")
        return None, []

def analyze_sentiment(reviews):
    """Анализирует тональность отзывов"""
    if not reviews:
        logger.warning("Передан пустой список отзывов для анализа")
        return None, None, None, None
    
    positive = 0
    neutral = 0
    negative = 0
    analyzed_count = 0
    
    for i, review in enumerate(reviews):
        try:
            # Ограничиваем длину для модели
            review_text = review[:1000]  # Уменьшил до 1000 для стабильности
            
            # Пропускаем слишком короткие отзывы
            if len(review_text.strip()) < 10:
                continue
                
            result = sentiment_analyzer(review_text)[0]
            label = result['label']
            score = result['score']
            
            # Модель blanchefort/rubert-base-sentiment возвращает английские метки
            if label == 'POSITIVE':
                positive += 1
            elif label == 'NEGATIVE':
                negative += 1
            elif label == 'NEUTRAL':
                neutral += 1
            else:
                # Резервная логика
                if score > 0.6:
                    positive += 1
                elif score < 0.4:
                    negative += 1
                else:
                    neutral += 1
            
            analyzed_count += 1
            logger.debug(f"Проанализирован отзыв {i+1}: {label} (score: {score})")
            
        except Exception as e:
            logger.error(f"Ошибка анализа отзыва {i+1}: {e}")
            neutral += 1  # В случае ошибки считаем нейтральным
            analyzed_count += 1
    
    # Защита от деления на ноль
    if analyzed_count == 0:
        logger.error("Не удалось проанализировать ни одного отзыва")
        return 0, 0, 0, 0
    
    total = analyzed_count
    positive_percent = (positive / total) * 100
    neutral_percent = (neutral / total) * 100
    negative_percent = (negative / total) * 100
    
    # Расчет общего рейтинга (1-5)
    overall_rating = 1 + (positive_percent / 100) * 4
    
    logger.info(f"Результаты анализа: положительные {positive_percent:.1f}%, "
                f"нейтральные {neutral_percent:.1f}%, отрицательные {negative_percent:.1f}%")
    
    return positive_percent, neutral_percent, negative_percent, overall_rating

@app.route('/')
def index():
    """Главная страница"""
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Страница авторизации"""
    # Если пользователь уже авторизован, перенаправляем
    if current_user.is_authenticated:
        return redirect(url_for('instruction'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            flash('Заполните все поля', 'error')
            return render_template('login.html')
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user, remember=True)
            flash('Успешный вход!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('instruction'))
        else:
            flash('Неверное имя пользователя или пароль', 'error')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Регистрация"""
    if current_user.is_authenticated:
        return redirect(url_for('instruction'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        
        # Валидация
        if not all([username, email, password]):
            flash('Заполните все поля', 'error')
            return render_template('login.html', register=True)
        
        if len(username) < 3:
            flash('Имя пользователя должно содержать минимум 3 символа', 'error')
            return render_template('login.html', register=True)
        
        if len(password) < 6:
            flash('Пароль должен содержать минимум 6 символов', 'error')
            return render_template('login.html', register=True)
        
        if User.query.filter_by(username=username).first():
            flash('Пользователь с таким именем уже существует', 'error')
            return render_template('login.html', register=True)
        
        if User.query.filter_by(email=email).first():
            flash('Пользователь с таким email уже существует', 'error')
            return render_template('login.html', register=True)
        
        # Создаем пользователя
        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password)
        )
        
        try:
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash('Регистрация успешна!', 'success')
            return redirect(url_for('instruction'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Ошибка при регистрации: {e}")
            flash('Ошибка при регистрации', 'error')
    
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
    url = request.form.get('url', '').strip()
    
    if not url:
        flash('Пожалуйста, введите ссылку на товар', 'error')
        return redirect(url_for('instruction'))
    
    # Проверяем URL
    if not is_valid_irecommend_url(url):
        flash('Пожалуйста, введите корректную ссылку с сайта irecommend.ru или irecommend.com', 'error')
        return redirect(url_for('instruction'))
    
    # Проверяем, что модель загружена
    if sentiment_analyzer is None:
        flash('Система анализа временно недоступна. Пожалуйста, попробуйте позже.', 'error')
        return redirect(url_for('instruction'))
    
    try:
        # Парсим отзывы
        start_time = time.time()
        product_name, reviews = parse_reviews(url)
        parse_time = time.time() - start_time
        
        logger.info(f"Парсинг занял {parse_time:.2f} секунд, найдено {len(reviews)} отзывов")
        
        if not reviews:
            flash('Не удалось найти отзывы для анализа. Проверьте ссылку или попробуйте другой товар.', 'error')
            return redirect(url_for('instruction'))
        
        # Предупреждение при малом количестве отзывов
        if len(reviews) < 3:
            flash('Внимание: найдено очень мало отзывов. Результат может быть неточным.', 'warning')
        
        # Анализируем тональность
        analysis_start = time.time()
        positive, neutral, negative, overall_rating = analyze_sentiment(reviews)
        analysis_time = time.time() - analysis_start
        
        logger.info(f"Анализ тональности занял {analysis_time:.2f} секунд")
        
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
            overall_rating=overall_rating,
            reviews_count=len(reviews)
        )
        
        db.session.add(analysis)
        db.session.commit()
        
        # Добавляем информацию о времени обработки
        processing_info = f"Обработано {len(reviews)} отзывов за {parse_time + analysis_time:.1f} сек"
        
        return render_template('results.html',
                            product_name=product_name,
                            positive=round(positive, 1),
                            neutral=round(neutral, 1),
                            negative=round(negative, 1),
                            overall_rating=round(overall_rating, 2),
                            reviews_count=len(reviews),
                            processing_info=processing_info)
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети: {e}")
        flash('Ошибка при обращении к сайту. Проверьте ссылку и попробуйте снова.', 'error')
        return redirect(url_for('instruction'))
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
        flash('Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже.', 'error')
        return redirect(url_for('instruction'))

@app.route('/profile')
@login_required
def profile():
    """Страница профиля"""
    analyses = Analysis.query.filter_by(user_id=current_user.id).order_by(Analysis.created_at.desc()).limit(50).all()
    return render_template('profile.html', user=current_user, analyses=analyses)

@app.route('/analysis/<int:analysis_id>')
@login_required
def analysis_detail(analysis_id):
    """Детальная страница анализа"""
    analysis = Analysis.query.filter_by(id=analysis_id, user_id=current_user.id).first_or_404()
    return render_template('analysis_detail.html', analysis=analysis)

@app.route('/logout')
@login_required
def logout():
    """Выход из системы"""
    logout_user()
    flash('Вы успешно вышли из системы', 'info')
    return redirect(url_for('index'))

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    
    if debug:
        app.run(debug=True, host='0.0.0.0', port=port)
    else:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port)
