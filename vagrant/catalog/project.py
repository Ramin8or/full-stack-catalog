from flask import Flask, render_template, request, redirect
from flask import jsonify, url_for, flash, make_response
from flask import session as login_session

from sqlalchemy import create_engine, asc, desc
from sqlalchemy.orm import sessionmaker

from database_setup import Base, Category, Item, User

from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError

from werkzeug import secure_filename
from werkzeug.contrib.atom import AtomFeed
from urlparse import urljoin

import random, string, httplib2, json, requests, os, datetime

# Constants
ALL_CATEGORIES_ID = 1
ALL_CATEGORIES = "All Categories"
DEFAULT_CAT = "general"
SHOW_LIMIT = 12 # Limit for number of recent tems shown in catalog
APPLICATION_NAME = "Catalog Application"
CLIENT_ID = json.loads(
    open('client_secrets.json', 'r').read())['web']['client_id']
UPLOAD_FOLDER = 'static/'
ALLOWED_EXTENSIONS = set(['png', 'jpg', 'jpeg', 'gif'])

# Start Flask
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Connect to Database and create database session
engine = create_engine('sqlite:///catalog.db')
Base.metadata.bind = engine
DBSession = sessionmaker(bind=engine)
db_session = DBSession()

# Create anti-forgery state token
@app.route('/login')
def showLogin():
    state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                    for x in xrange(32))
    login_session['state'] = state
    return render_template('login.html', STATE=state)

# Handle connect
@app.route('/gconnect', methods=['POST'])
def gconnect():
    # Validate state token
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    # Obtain authorization code
    code = request.data
    try:
        # Upgrade the authorization code into a credentials object
        oauth_flow = flow_from_clientsecrets('client_secrets.json', scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(
            json.dumps('Failed to upgrade the authorization code.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    # Check that the access token is valid.
    access_token = credentials.access_token
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
           % access_token)
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])
    # If there was an error in the access token info, abort.
    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'
    # Verify that the access token is used for the intended user.
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(
            json.dumps("Token's user ID doesn't match given user ID."), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    # Verify that the access token is valid for this app.
    if result['issued_to'] != CLIENT_ID:
        response = make_response(
            json.dumps("Token's client ID does not match app's."), 401)
        print "Token's client ID does not match the app's."
        response.headers['Content-Type'] = 'application/json'
        return response
    # Is current user already connected?
    stored_credentials = login_session.get('credentials')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_credentials is not None and gplus_id == stored_gplus_id:
        response = make_response(json.dumps('Current user is already connected.'),
                                 200)
        response.headers['Content-Type'] = 'application/json'
        return response
    # Store the access token in the session for later use.
    login_session['credentials'] = credentials.access_token
    login_session['gplus_id'] = gplus_id
    # Get user info
    userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': credentials.access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)
    data = answer.json()
    login_session['username'] = data['name']
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']
    login_session['provider'] = 'google'
    # see if user exists, if it doesn't make a new one
    user_id = getUserID(data["email"])
    if not user_id:
        user_id = createUser(login_session)
    login_session['user_id'] = user_id
    output = ''
    output += '<h1>Welcome, '
    output += login_session['username']
    output += '!</h1>'
    output += '<img src="'
    output += login_session['picture']
    output += ' " style = "width: 300px; height: 300px;border-radius: 150px;-webkit-border-radius: 150px;-moz-border-radius: 150px;"> '
    flash("you are now logged in as %s" % login_session['username'])
    return output

# User Helper Functions
def createUser(login_session):
    newUser = User(name=login_session['username'], email=login_session[
                   'email'], picture=login_session['picture'])
    db_session.add(newUser)
    db_session.commit()
    user = db_session.query(User).filter_by(email=login_session['email']).one()
    return user.id

def getUserInfo(user_id):
    user = db_session.query(User).filter_by(id=user_id).one()
    return user

def getUserID(email):
    try:
        user = db_session.query(User).filter_by(email=email).one()
        return user.id
    except:
        return None

# Handle Disconnect - Revoke a current user's token and reset their login_session
@app.route('/gdisconnect')
def gdisconnect():
    # Only disconnect a connected user.
    credentials = login_session.get('credentials')
    if credentials is None:
        response = make_response(
            json.dumps('Current user not connected.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    url = 'https://accounts.google.com/o/oauth2/revoke?token=%s' % credentials
    h = httplib2.Http()
    result = h.request(url, 'GET')[0]

    if result['status'] != '200':
        # For whatever reason, the given token was invalid.
        response = make_response(
            json.dumps('Failed to revoke token for given user.', 400))
        response.headers['Content-Type'] = 'application/json'
        return response

# Disconnect based on provider
@app.route('/disconnect')
def disconnect():
    if 'provider' in login_session:
        if login_session['provider'] == 'google':
            gdisconnect()
            del login_session['gplus_id']
            del login_session['credentials']
        del login_session['username']
        del login_session['email']
        del login_session['picture']
        del login_session['user_id']
        del login_session['provider']
        flash("You have successfully been logged out.")
        return redirect(url_for('showCatalog'))
    else:
        flash("You were not logged in")
        return redirect(url_for('showCatalog'))

# Show catalog
@app.route('/')
@app.route('/catalog')
def showCatalog():
    categories = db_session.query(Category).order_by(asc(Category.name))
    all_cat = categories.filter_by(name=ALL_CATEGORIES).one()
    items = db_session.query(
            Item).order_by(desc(Item.id)).limit(SHOW_LIMIT).all()
    return render_template('catalog.html', 
                            categories=categories, 
                            category=all_cat, items=items)

# Show items for selected category
@app.route('/catalog/<category_name>/')
def showCategory(category_name):
    categories = db_session.query(Category).order_by(asc(Category.name))
    category = categories.filter_by(name=category_name).one()
    if category.id == ALL_CATEGORIES_ID:
        items = db_session.query(Item).all()
    else:
        items = db_session.query(Item).filter_by(category_id=category.id).all()
    count = len(items)
    return render_template('category.html', 
                            categories=categories, 
                            category=category, items=items, 
                            count=count)

# Show item
@app.route('/catalog/<category_name>/<item_name>')
def showItem(item_name, category_name):
    try:
        item = db_session.query(Item).filter_by(name=item_name).one()
    except:
        flash("Item by the name of %s does not exist!" % (item_name))
        return redirect(url_for('showCategory', category_name=category_name))        
    return render_template('item.html', item=item, category_name=category_name)

# Edit an item using POST
@app.route('/catalog/<item_name>/edit', methods=['GET', 'POST'])
def editItem(item_name):
    if 'username' not in login_session:
        return redirect('/login')
    try:
        item = db_session.query(Item).filter_by(name=item_name).one()
    except:
        flash("Item by the name of %s does not exist!" % (item_name))
        return redirect(url_for('/'))
 
    category = item.category

    if login_session['user_id'] != item.user_id:
        return "<script>function myFunction() {alert('You are not authorized to edit this item.');}</script><body onload='myFunction()''>"
    if request.method == 'POST':
        # Note since Name is used for routing, it cannot be changed
        if request.form['description']:
            item.description = request.form['description']
        if request.form['price']:
            item.price = request.form['price']
        db_session.add(item)
        db_session.commit()
        flash('Item Successfully Edited')
        return redirect(url_for('showItem', 
                                item_name=item_name, 
                                category_name=category.name))
    else:
        return render_template('editItem.html', item=item)


def savePicture(file, id):
    '''
    Save uploaded picture for an item into static folder.
    Return the filename of the picture which will be id of item
    with random string for uniqueness plus the extension.
    '''
    # TODO Check file is not null
    extension = file.filename.rsplit('.', 1)[1]
    if extension.lower() in ALLOWED_EXTENSIONS:
        # Make filename unique and secure
        filename = str(id)+"_"+secure_filename(file.filename)
        # Save it in upload_folder
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return filename
    else:
        flash("Unable to save uploaded picture.")
        return ""

# Add a new item 
@app.route('/catalog/newItem/', methods=['GET', 'POST'])
def newItem():
    if 'username' not in login_session:
        return redirect('/login')
    categories = db_session.query(Category).order_by(asc(Category.name))

    if request.method == 'POST':
        if request.form['name']:
            item_name = request.form['name']
        if request.form['category']:
            item_cat = request.form['category']
            if item_cat == ALL_CATEGORIES:
                item_cat = DEFAULT_CAT
        else:
            item_cat = DEFAULT_CAT
        if request.form['description']:
            item_desc = request.form['description']
        else:
            item_desc = ""
        if request.form['price']:
            item_price = request.form['price']
        else:
            item_price = ""

        try:
            category = db_session.query(Category).filter_by(name=item_cat).one()
            newItem = Item(name=item_name, 
                           description=item_desc, 
                           price=item_price,
                           category_id=category.id, 
                           picture="", 
                           user_id=login_session['user_id'])
            db_session.add(newItem)
            db_session.commit()

            # If picture, save with unique name to static folder and update item.
            if request.files['picture']:
                newItem.picture = savePicture(request.files['picture'], 
                                              newItem.id)
                db_session.commit()

            flash('Successfully Created: %s' % (newItem.name))
            return redirect(url_for('showItem', 
                                    item_name=newItem.name, 
                                    category_name=newItem.category.name))
        except:
            flash('Invalid input, could not create new item. Please specify a unique name, and use a category.')
            return render_template('newItem.html', categories=categories)

    else:
        return render_template('newItem.html', categories=categories)

# Delete an item using POST for safety
@app.route('/catalog/<item_name>/delete', methods=['GET', 'POST'])
def deleteItem(item_name):
    if 'username' not in login_session:
        return redirect('/login')

    try:
        item = db_session.query(Item).filter_by(name=item_name).one()
    except:
        flash("Item by the name of %s does not exist!" % (item_name))
        return redirect(url_for('/'))
    if item.user_id != login_session['user_id']:
        return "<script>function myFunction() {alert('You are not authorized to delete this item.');}</script><body onload='myFunction()''>"
    if request.method == 'POST':
        category_name = item.category.name
        db_session.delete(item)
        db_session.commit()
        flash('%s Successfully Deleted' % item.name)
        return redirect(url_for('showCategory', category_name=category_name))
    else:
        return render_template('deleteItem.html', item=item)

# JSON Catalog
@app.route('/catalog/JSON')
def catalogJSON():
    catalog = db_session.query(Category).all()
    return jsonify(catalog=[r.serialize for r in catalog])

# Helper function for catalogATOM
def make_external(url):
    return urljoin(request.url_root, url)

# ATOM Catalog
# Taken from http://flask.pocoo.org/snippets/10/
@app.route('/catalog/ATOM')
def catalogATOM():
    feed = AtomFeed(APPLICATION_NAME, 
                    feed_url=request.url, url=request.url_root)
    items = db_session.query(Item).all()
    for item in items:
        feed.add(item.name, unicode(item.description),
                 content_type='html',
                 author=item.user.name,
                 url=make_external(url_for('showItem', 
                                    item_name=item.name, 
                                    category_name=item.category.name)),
                 updated=datetime.datetime.now())
    return feed.get_response()

# Main application
if __name__ == '__main__':
    app.secret_key = 'super_secret_key'
    app.debug = True
    app.run(host='0.0.0.0', port=8000)
