# -*- coding: utf-8 -*-

from resarmll import settings
from resa.models import Article
from resautils.currency import currency_alt

CART_KEY = 'CART_KEY'

class CartItem:
    id = 0
    quantity = 0
    label = ""
    price = 0.0
    salt = ''
    sorting = 0

    def __init__(self, id=0, quantity=0, label="", price=0.0, sorting=0):
        self.id = id
        self.quantity = quantity
        self.label = label
        self.price = price
        self.sorting = sorting

    def price_alt(self):
        return currency_alt(self.price)

    def total(self):
        return self.quantity*self.price

    def total_alt(self):
        return currency_alt(self.total())

    def stock(self):
        product = Article.objects.get(id=self.id)
        return product.quantity()

class Cart:
    def __init__(self, request, salt=None):
        self.items = []
        self.donation = 0
        self.request = request
        self.salt = str(salt)
        savedcart = request.session.get(self.get_salt())
        if savedcart is not None:
            donation, data = savedcart
            products = {}
            for t in data:
                i,d = t
                products[i] = d
            self.add_group(products)
            self.donation = donation

    def __iter__(self):
        for item in self.items:
            yield item

    def get_salt(self):
        ret = CART_KEY
        if self.salt != '':
            ret = CART_KEY+'_'+self.salt
        return ret

    def add_group(self, products):
        prods = Article.objects.filter(id__in=products.keys()).order_by('order')
        for product in prods:
            self.items.append(CartItem(product.id, products[product.id],
                product.title(), product.price, product.order))

    def add(self, product_id, quantity, replace=False):
        ret = False
        for i,item in enumerate(self.items):
            if item.id == product_id:
                if replace:
                    self.items[i].quantity = quantity
                else:
                    self.items[i].quantity += quantity
                ret = True
                break
        if not ret:
            try:
                product = Article.objects.get(id=product_id)
                self.items.append(CartItem(product_id, quantity,
                    product.title(), product.price, product.order))
                ret = True
            except:
                pass
        # sorting
        self.items = sorted(self.items, key=lambda i: i.sorting)
        return ret

    def delete(self, product_id):
        ret = False
        for i,item in enumerate(self.items):
            if item.id == product_id:
                del self.items[i]
                ret = True
                break
        return ret

    def update(self, product_id, quantity):
        if quantity < 0:
            quantity = 0
        if quantity == 0:
            ret = self.delete(product_id)
        else:
            ret = self.add(product_id, quantity, True)
        return ret

    def empty(self):
        return len(self.items) == 0 and self.donation == 0

    def total(self):
        ret = self.donation
        for item in self.items:
            ret += item.total()
        return ret

    def total_alt(self):
        return currency_alt(self.total())

    def save(self, request):
        session_data = []
        for i,item in enumerate(self.items):
            session_data.append((item.id, item.quantity))
        request.session[self.get_salt()] = (self.donation, session_data)

    def clear(self):
        self.items = []

    def is_valid(self):
        ret = self.donation >= 0
        for i,item in enumerate(self.items):
            product = Article.objects.get(id=item.id)
            ret = ret and product.quantity() >= item.quantity
        return ret

    def has_gcs_ckecked(self):
        ret = False
        if not settings.CART_SETTINGS['gcsuse'] or \
            (self.request.POST.has_key('accept_gcs') and self.request.POST['accept_gcs'] == '1'):
            ret = True
        return ret
