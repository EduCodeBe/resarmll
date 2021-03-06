# -*- coding: utf-8 -*-
import os, time, csv
from datetime import datetime as date
from decimal import Decimal as dec

from django.db.models import Count, Sum
from django.utils.translation import ugettext_lazy as _
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseNotFound
from django.core.servers.basehttp import FileWrapper
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.mail import send_mail

from models import Article, Country
from forms import PayOrderForm, DelOrderForm
from orders import Order, OrderDetail
from cart import Cart
from stock import Stock
from bank.cyberplus import CyberPlus
from bank.etransactions import eTransactions
from bank.cmcic import cmcic
from bank.ogone import ogone
from bank.paypal import Paypal
from resarmll import settings
from resautils.decorators import auto_render, staff_required, manager_required, reception_required
from resautils.pdf import gen_pdf
from compta.models import Operation

@auto_render
def gcs(request, tmpl):
    params = {}
    if not settings.CART_SETTINGS['gcsuse']:
        return HttpResponseRedirect('/')
    return tmpl, params

@login_required
@auto_render
def catalog_list(request, tmpl):
    products = Article.objects.filter(enabled=True).order_by('order')
    currency = settings.CURRENCY
    currency_alt = settings.CURRENCY_ALT
    return tmpl, locals()

@login_required
@auto_render
def cart_list(request, tmpl, action=None, product_id=None):
    cart = Cart(request)
    msg_ok = msg_err = None
    if action == 'del':
        if cart.delete(int(product_id)):
            msg_ok = _(u"Product successfully removed from cart")
        else:
            msg_err = _(u"Error while removing product from cart")
    elif action == 'add' or action == 'update':
        statusok = False
        if len(request.POST)>0:
            statusok = True
            for k,v  in request.POST.iteritems():
                product_id = 0
                try:
                    quantity = int(v)
                except:
                    quantity = 0
                if k.startswith('product_'):
                    product_id = int(k[8:])
                    if action == 'add':
                        statusok = statusok and cart.add(product_id, quantity)
                    else:
                        statusok = statusok and cart.update(product_id, quantity)
                if k == 'donation':
                    statusok = statusok and quantity >= 0
                    if quantity >= 0:
                        cart.donation = quantity
        if statusok:
            msg_ok = _(u"Product(s) successfully added") if action == 'add' else _(u"Product(s) successfully updated")
        else:
            msg_err = _(u"Error while adding product(s)") if action == 'add' else _(u"Error while updating product(s)")
    elif action == 'invalid':
        msg_err = _(u"Unable to confirm your order, one (or more) product(s) in your cart exceed the available quantity")
    elif action == 'uncheckedgcs':
        msg_err = _(u"You have to read and accept the general terms and conditions of sales in order to confirm your order")
    cart.save(request)
    return tmpl, {
        'cart': cart,
        'msg_err': msg_err, 'msg_ok': msg_ok,
        'usegcs': settings.CART_SETTINGS['gcsuse'],
        'currency': settings.CURRENCY, 'currency_alt': settings.CURRENCY_ALT,
        }

@login_required
@auto_render
def orders_list(request, tmpl, action=None):
    msg_ok = msg_err = None
    if action == 'validate':
        cart = Cart(request)
        if not cart.has_gcs_ckecked():
            return HttpResponseRedirect('/resa/cart/uncheckedgcs/')
        if not cart.is_valid():
            return HttpResponseRedirect('/resa/cart/invalid/')
        elif not cart.empty():
            order = Order(user=request.user, creation_date=date.now(), donation = cart.donation)
            order.save_confirm(cart)
            cart.clear()
            cart.save(request)
            msg_ok = _(u"Order successfully confirmed")

    pending_orders = request.user.order_set.filter(payment_date__isnull=True)
    validated_orders = request.user.order_set.filter(payment_date__isnull=False)
    return tmpl, {
        'pending_orders': pending_orders,
        'validated_orders': validated_orders,
        'msg_err': msg_err, 'msg_ok': msg_ok,
        'user_obj': request.user,
        'currency': settings.CURRENCY, 'currency_alt': settings.CURRENCY_ALT,
    }

@login_required
@auto_render
def orders_details(request, tmpl, order_id=0):
    try:
        order = Order.objects.get(user=request.user, id=int(order_id))
    except:
        order = None

    # only allowed to see its own orders
    if order and order.user.id != request.user.id:
        order = None

    protocol = request.is_secure() and 'https' or 'http'
    url = "%s://%s" % (protocol, request.get_host())
    paypal = settings.PAYPAL_SETTINGS
    treasurer = settings.TREASURER_SETTINGS
    wiretransfer = settings.WIRETRANSFER_SETTINGS
    check = settings.CHECK_SETTINGS
    bank = settings.BANK_DRIVER

    ip_addr = request.META['REMOTE_ADDR']
    if order and bank:
        if bank.upper() == 'CYBERPLUS':
            bp_tmpl = 'resa/orders_details_cyberplus.html'
            bp = CyberPlus(request)
            bp_err, bp_code, bp_form = bp.form(order, request.user, request.LANGUAGE_CODE, ip_addr, url)
        elif bank.upper() == 'ETRANSACTIONS':
            bp_tmpl = 'resa/orders_details_etransactions.html'
        elif bank.upper() == 'CMCIC':
            bp_tmpl = 'resa/orders_details_cmcic.html'
            bp = cmcic(request)
            bp_form = bp.form(order, request.user, request.LANGUAGE_CODE, url)
        elif bank.upper() == 'OGONE':
            bp_tmpl = 'resa/orders_details_ogone.html'
            bp = ogone(request)
            bp_form = bp.form(order, request.user, request.LANGUAGE_CODE, url)

    currency = settings.CURRENCY
    currency_alt = settings.CURRENCY_ALT

    return tmpl, locals()

@login_required
@auto_render
def orders_delete(request, order_id=0):
    try:
        order = Order.objects.get(user=request.user, id=int(order_id))
    except:
        order = None

    # only allowed to see its own orders
    if order and order.user.id != request.user.id:
        order = None

    if not order:
        return HttpResponseRedirect('/resa/orders/details/'+order_id)
    order.remove()

    return HttpResponseRedirect('/resa/orders/')

@login_required
def orders_pdf(request, tmpl, order_id=0):
    try:
        order_id = int(order_id)
        order = Order.objects.get(id=order_id)
    except:
        order = None

    # only allowed to see its own orders
    if not order or (order and order.user.id != request.user.id):
        return HttpResponseRedirect('/resa/orders/details/%d' % order_id)

    response = HttpResponse(mimetype='application/pdf')
    response['Content-Disposition'] = 'attachment; filename=invoice_order_%d.pdf' % order_id
    response.write(gen_pdf(tmpl, {'user': request.user, 'order': order, 'lang': request.LANGUAGE_CODE[0:2],
                        'address_lines': settings.FULL_ADDRESS.strip().split("\n"),
                        'currency': settings.CURRENCY,
                        'tva': settings.TVA['value'],
                        'invoice_msg_frenchtaxcode': settings.TVA['invoice_msg_frenchtaxcode'],
                        'invoice_msg_notaxes': settings.TVA['invoice_msg_notaxes']}))
    return response

@login_required
@reception_required
@auto_render
def orders_search(request, tmpl):
    noresults = False
    if request.method == 'POST':
        try:
            order = Order.objects.get(id=int(request.POST['pattern']))
        except:
            order = None
        if order:
            return HttpResponseRedirect('/resa/manage_orders/'+str(order.user_id))
        else:
            noresults = True
    return tmpl, locals()

@login_required
@manager_required
@auto_render
def orders_notpaid(request, tmpl):
    results = User.objects.filter(order__payment_date__isnull=True).annotate(num_orders=Count('order')).filter(num_orders__gt=0).order_by('last_name')
    results_orga = [u for u in results if u.get_profile().badge_type.section == 'orga']
    results_speakers = [u for u in results if u.get_profile().badge_type.section == 'speakers']
    results_others = [u for u in results if u.get_profile().badge_type.section != 'orga' and u.get_profile().badge_type.section != 'speakers']

    mail_sender_name = request.user.get_full_name()
    mail_sender_email = settings.TREASURER_SETTINGS['email']
    mail_subject = request.POST['subject'] if request.POST.has_key('subject') else ''
    mail_body = request.POST['body'] if request.POST.has_key('body') else ''
    selected_users = []

    msg_err = msg_ok = None

    if len(request.POST) > 0:
        for k,v  in request.POST.iteritems():
            if k.startswith('user_'):
                selected_users.append(int(k[5:]))

        if mail_body.strip() == '':
            msg_err = _("Email body should not be empty")
        if mail_subject.strip() == '':
            msg_err = _("Email subject should not be empty")
        if selected_users == []:
            msg_err = _("At least one user should be selected")

        if msg_err == None:
            users = User.objects.filter(id__in=selected_users)
            emailfrom = "%s <%s>" % (mail_sender_name, mail_sender_email)
            for user in users:
                if send_mail(mail_subject, mail_body, emailfrom, [user.email]) != 1:
                    msg_err = _("One or more emails were not sent correctly")

            if msg_err == None:
                msg_ok = _("All emails were sucessfully sent")
                mail_subject = ''
                mail_body = ''
                selected_users = []

    return tmpl, locals()

@login_required
@auto_render
def orders_gcs(request, tmpl):
    params = {}
    return tmpl, params

@login_required
@reception_required
@auto_render
def manage_orders(request, tmpl, user_id=None):
    user = None
    if user_id:
        try:
            user = User.objects.get(id=user_id)
        except:
            user = None

    params = {
        'user_obj': user,
        'msg_ok': None,
        'msg_err': None,
        'currency': settings.CURRENCY,
        'currency_alt': settings.CURRENCY_ALT,
    }
    if user:
        form = form_del = None
        if request.method == 'POST':
            try:
                order = Order.objects.get(id=int(request.POST['order_id']))
            except:
                order = None

            # mark order as paid
            form = PayOrderForm(request.POST)
            if form.is_valid() and order:
                    order.save_paid(form.cleaned_data['method'], form.cleaned_data['note'])
                    params['msg_ok'] = _(u"Order sucessfully marked as paid")
                    form = None

            # remove order definitly
            form_del = DelOrderForm(request.POST)
            if form_del.is_valid() and order:
                    order.remove()
                    params['msg_ok'] = _(u"Order sucessfully removed")
                    form_del = None

            # change quantities distributed
            if request.POST.get('distribution') == '1' and order:
                for k,v  in request.POST.iteritems():
                    if k.startswith('orderdetail_'):
                        orderdetail_id = int(k[12:])
                        od = OrderDetail.objects.get(id=orderdetail_id)
                        od.distributed = int(request.POST[k])
                        od.save()
                params['msg_ok'] = _(u"Order sucessfully updated")

        if not form:
            form = PayOrderForm()
        if not form_del:
            form_del = DelOrderForm()

        params['form'] = form
        params['form_del'] = form_del
        params['pending_orders'] = user.order_set.filter(payment_date__isnull=True)
        params['validated_orders'] = user.order_set.filter(payment_date__isnull=False)
    return tmpl, params

@login_required
@reception_required
def manage_orders_pdf(request, tmpl, order_id=0):
    try:
        order_id = int(order_id)
        order = Order.objects.get(id=order_id)
    except:
        order = None

    if not order:
        return HttpResponseRedirect('/')

    response = HttpResponse(mimetype='application/pdf')
    response['Content-Disposition'] = 'attachment; filename=invoice_order_%d.pdf' % order_id
    response.write(gen_pdf(tmpl, {'user': order.user, 'order': order, 'lang': request.LANGUAGE_CODE[0:2],
                        'address_lines': settings.FULL_ADDRESS.strip().split("\n"),
                        'currency': settings.CURRENCY,
                        'tva': settings.TVA['value'],
                        'invoice_msg_frenchtaxcode': settings.TVA['invoice_msg_frenchtaxcode'],
                        'invoice_msg_notaxes': settings.TVA['invoice_msg_notaxes'],}))
    return response

@login_required
@reception_required
@auto_render
def manage_cart(request, tmpl, user_id=None, action=None, product_id=None):
    user = None
    if user_id:
        try:
            user = User.objects.get(id=user_id)
        except:
            user = None
    msg_ok = msg_err = products = cart = None
    products = None
    if user:
        cart = Cart(request, user.id)
        if request.user.is_staff:
            products = Article.objects.all().order_by('order')
        else:
            products = Article.objects.filter(enabled=True).order_by('order')
        if action == 'add':
            product_id = int(request.POST.get('cart_add'))
            if cart.add(product_id, 1):
                msg_ok = _(u"Product successfully added to cart")
            else:
                msg_err = _(u"Error while adding product to cart")
        elif action == 'del':
            if cart.delete(int(product_id)):
                msg_ok = _(u"Product successfully removed from cart")
            else:
                msg_err = _(u"Error while removing product from cart")
        elif action == 'update':
            update = True
            for k,v  in request.POST.iteritems():
                product_id = 0
                try:
                    quantity = int(v)
                except:
                    quantity = 0
                if k.startswith('product_'):
                    product_id = int(k[8:])
                    update = update and cart.update(product_id, quantity)
            if update:
                msg_ok = _(u"Product(s) successfully updated")
            else:
                msg_err = _(u"Error while updating product(s)")
        elif action == 'validate':
            valid = cart.is_valid()
            if not valid and request.user.is_staff:
                valid = request.POST.get('force') == '1'
            if not valid:
                msg_err = _(u"Unable to confirm this order, one (or more) product(s) in the cart exceed the available quantity")
            else:
                order = Order(user=user, creation_date=date.now())
                order.save_confirm(cart)
                cart.clear()
                msg_ok = _(u"Order successfully confirmed")
        cart.save(request)
    return tmpl, {
        'user_obj': user,
        'products': products,
        'cart': cart,
        'msg_err': msg_err, 'msg_ok': msg_ok,
        'is_admin': request.user.is_staff,
        'currency': settings.CURRENCY, 'currency_alt': settings.CURRENCY_ALT,
    }

@login_required
@staff_required
@auto_render
def manage_compta(request, tmpl, user_id=None):
    operations = user = None
    if user_id:
        try:
            user = User.objects.get(id=user_id)
        except:
            user = None
    if user:
        operations = Operation.objects.filter(user=user).order_by('order', 'id')
        solde = dec(0)
        for i,op in enumerate(operations):
            if op.date_payment:
                solde -= op.amount
            else:
                solde += op.amount
            operations[i].set_solde(solde)
    return tmpl, {'user_obj': user, 'operations': operations}

@login_required
@manager_required
@auto_render
def stats(request, tmpl):
    stats_countries = Country.objects.annotate(num_users=Count('userprofile')).filter(num_users__gt=0).order_by('-num_users')
    return tmpl, locals()

@login_required
@manager_required
@auto_render
def stocks(request, tmpl):
    stocks = Stock.objects.all().order_by('order')
    return tmpl, locals()

@login_required
@manager_required
@auto_render
def sales(request, tmpl):
    products = Article.objects.order_by('order')
    results_paid = results_notpaid = product = None
    if request.method == 'POST':
        try:
            product = Article.objects.get(id=int(request.POST['product']))
        except:
            product = None
        if product:
            results_paid = User.objects.filter(order__payment_date__isnull=False,order__orderdetail__product=product).annotate(num_products=Sum('order__orderdetail__quantity')).filter().order_by('last_name')
            results_notpaid = User.objects.filter(order__payment_date__isnull=True,order__orderdetail__product=product).annotate(num_products=Sum('order__orderdetail__quantity')).filter().order_by('last_name')
    return tmpl, locals()

@login_required
@manager_required
def sales_export(request, product_id):
    response = HttpResponse(mimetype='text/csv')
    try:
        product = Article.objects.get(id=product_id)
    except:
        product = None
    if product:
        response = HttpResponse(mimetype='text/csv')
        response['Content-Disposition'] = 'attachement; filename=export_product_%d_%s.csv' % (product.id, time.strftime('%Y%m%d-%H%M%S',  time.localtime()))
        writer = csv.writer(response, delimiter=';', quoting=csv.QUOTE_NONNUMERIC)
        results =               User.objects.filter(order__payment_date__isnull=False,order__orderdetail__product=product).annotate(num_ordered=Sum('order__orderdetail__quantity'), num_distributed=Sum('order__orderdetail__distributed')).filter().order_by('last_name')
        if results:
            for user in results:
                fields = []
                fields.append(_("Last name").encode('utf-8'))
                fields.append(_("First name").encode('utf-8'))
                fields.append(_("Product").encode('utf-8'))
                fields.append(_("Number of products ordered").encode('utf-8'))
                fields.append(_("Number of products distributed").encode('utf-8'))
                fields.append(_("Number of products not yet distributed").encode('utf-8'))
                fields.append(_("Email").encode('utf-8'))
                fields.append(_("Address").encode('utf-8'))
                writer.writerow(fields)
                if user.num_ordered-user.num_distributed > 0:
                    fields = []
                    fields.append(user.last_name.encode('utf-8'))
                    fields.append(user.first_name.encode('utf-8'))
                    fields.append(product.label().encode('utf-8'))
                    fields.append(user.num_ordered)
                    fields.append(user.num_distributed)
                    fields.append(user.num_ordered-user.num_distributed)
                    fields.append(user.email)
                    fields.append(user.get_profile().address.encode('utf-8').strip())
                    writer.writerow(fields)
    return response

@login_required
@manager_required
@auto_render
def documents(request, tmpl):
    documents = {}
    cpdf = '%s/all_badges.pdf' % (settings.DOCUMENTSDIR)
    if os.path.exists(cpdf):
        documents['all_badges'] = {'path': cpdf, 'size': 0, 'mtime': 0}
    cpdf = '%s/all_invoices.pdf' % (settings.DOCUMENTSDIR)
    if os.path.exists(cpdf):
        documents['all_invoices'] = {'path': cpdf, 'size': 0, 'mtime': 0}
    cpdf = '%s/all_invoices_not_paid.pdf' % (settings.DOCUMENTSDIR)
    if os.path.exists(cpdf):
        documents['all_invoices_not_paid'] = {'path': cpdf, 'size': 0, 'mtime': 0}

    if documents:
        for k in documents:
            fsize = os.path.getsize(documents[k]['path'])
            fmtime = int(os.path.getmtime(documents[k]['path']))
            documents[k]['size'] = "%0.2f" % float(fsize/(1024.0*1024.0))
            documents[k]['mtime'] = time.strftime('%d/%m/%Y %H:%M:%S',time.localtime(fmtime))

    return tmpl, locals()

@login_required
@manager_required
def documents_view(request, docid):
    response = HttpResponseNotFound()
    doc = '%s/%s.pdf' % (settings.DOCUMENTSDIR, docid)
    ctime = time.strftime('%Y%m%d-%H%M%S')
    content_disposition = 'attachement; filename=%s_%s.pdf' % (docid, ctime)
    if os.path.exists(doc):
        wrapper = FileWrapper(file(doc))
        response = HttpResponse(wrapper, content_type='application/pdf')
        response['Content-Disposition'] = content_disposition
        response['Content-Length'] = os.path.getsize(doc)
    return response

@login_required
@auto_render
def orders_paypal_cancel(request, tmpl):
    msg_warn = _(u"Your payment has been canceled, you could resume it later.")
    return tmpl, locals()

@login_required
@auto_render
def orders_paypal_return(request, tmpl):
    msg_err = msg_ok = None
    try:
        order = Order.objects.get(id=int(request.POST.get('invoice')))
    except:
        order = None
    if order:
        if order.user.id != request.user.id:
            msg_err = _(u"This order is not yours.")
        else:
            if request.POST.get('payment_status') not in ['Completed', 'Pending']:
                msg_err = _(u"It seems that PayPal did not accept your payment (Code: %s).") % (request.POST.get('payment_status'))
            else:
                msg_ok = _(u"Your payment has been confirmed by PayPal, you should receive a notification by email in a few minutes.")
    else:
        msg_err = _(u"Unable to find the order related to this payment.")
    return tmpl, locals()

@auto_render
def orders_paypal_notify(request, order_id=0):
    p = Paypal(request)
    r = 'OK'
    if p.confirm():
        p.process_order()
    else:
        r = 'KO'
    return HttpResponse(r, mimetype="text/html")

@login_required
@auto_render
def orders_bank_return(request, tmpl, status=None, order_id=None):
    msg_err = msg_ok = msg_warn = None
    if settings.BANK_DRIVER.upper() == 'CYBERPLUS':
        bp = CyberPlus(request)
        error, code, canceled, rejected, delayed, accepted, order_id = bp.getreturn()
    elif settings.BANK_DRIVER.upper() == 'ETRANSACTIONS':
        bp = eTransactions(request)
        error, canceled, rejected, delayed, accepted, order_id = bp.getreturn()
    elif settings.BANK_DRIVER.upper() == 'CMCIC':
        bp = cmcic(request)
        canceled, rejected, delayed, accepted, order_id = bp.getreturn(status, order_id)
    elif settings.BANK_DRIVER.upper() == 'OGONE':
        bp = ogone(request)
        canceled, rejected, delayed, accepted, order_id = bp.getreturn(status)

    if delayed:
        msg_warn = _(u"Confirmation of your payment by your bank has not been received yet. You should contact your bank before retrying to pay for this order.")
    elif canceled:
        msg_warn = _(u"Your payment has been canceled, you could resume it later.")
    elif rejected:
        msg_err = _(u"Your payment has been rejected by the bank, you should retry in few days or try another payment method.")
    elif accepted:
        try:
            order = Order.objects.get(id=int(order_id))
        except:
            order = None
        if order:
            if order.user.id != request.user.id:
                msg_err = _(u"This order is not yours.")
            else:
                msg_ok = _(u"Your payment has been confirmed by the bank, you should receive a notification by email in a few minutes.")
        else:
            msg_err = _(u"Unable to find the order related to this payment.")
    else:
        msg_err = _(u"Your payment has failed, you should retry in few days or try another payment method.")
    return tmpl, locals()

def orders_bank_notify(request):
    r = 'OK'
    if settings.BANK_DRIVER.upper() == 'CYBERPLUS':
        bp = CyberPlus(request)
        if request.method == 'POST':
            bp.process_order()
        else:
            r = 'KO'
    elif settings.BANK_DRIVER.upper() == 'ETRANSACTIONS':
        bp = eTransactions(request)
        bp.process_order()
    elif settings.BANK_DRIVER.upper() == 'CMCIC':
        bp = cmcic(request)
        r = bp.process_order()
    elif settings.BANK_DRIVER.upper() == 'OGONE':
        bp = ogone(request)
        r = bp.process_order()

    return HttpResponse(r, mimetype="text/html")

def orders_etransactions_go(request, order_id=0):
    try:
        order = Order.objects.get(user=request.user, id=int(order_id))
    except:
        order = None

    # only allowed to see its own orders
    if not order or (order and order.user.id != request.user.id):
        return HttpResponseRedirect("/resa/orders/details/%d" % (int(order_id)))

    protocol = request.is_secure() and 'https' or 'http'
    url = "%s://%s" % (protocol, request.get_host())
    ip_addr = request.META['REMOTE_ADDR']

    bp = eTransactions(request)
    r = HttpResponse(mimetype='text/html')
    r['Cache-Control'] = 'no-cache, no-store'
    r['Pragma'] = 'no-cache'
    r.write(bp.form(order, request.user, request.LANGUAGE_CODE, ip_addr, url))

    return r
