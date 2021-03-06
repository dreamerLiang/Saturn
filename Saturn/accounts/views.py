from django.shortcuts import render, HttpResponseRedirect
from django.http import JsonResponse
from django.utils import timezone
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login as django_login, authenticate
from django.contrib.auth import logout as django_logout
from ratelimit.decorators import ratelimit
from django.forms.utils import ErrorList
from django.conf import settings
from accounts.models import Accounts
from section.models import File
from website.models import Website
from accounts.constants import ErrorCode
from accounts.forms import (
    SignupForm,
    SigninForm,
    EditUserProfileForm,
    ResetPasswordForm,
)
from website.forms import DeleteSiteForm
from utils.email import EmailService

import os


@ratelimit(key='get:email', rate='5/m', block=True)
def activate(request):
    if not ('verification_code' in request.GET and 'email' in request.GET):
        return HttpResponseRedirect('/accounts/signup/')

    email = request.GET['email']
    verification_code = request.GET['verification_code']

    if User.objects.filter(email=email).exists():
        user = User.objects.get(email=email)
    else:
        return HttpResponseRedirect('/accounts/signup/')

    if _is_bad_verification(email, verification_code):
        invalid_verification_code = True
    else:
        user.account.verified = True
        verification_success = True

    return render(request, "accounts/dashboard.html", locals())


def _is_bad_verification(email, verification_code):
    if not User.objects.filter(email=email).exists():
        return True
    user = User.objects.get(email=email)
    
    if user.account.verification_code != verification_code or \
            user.account.expire_at is not None and user.account.expire_at < timezone.now():
        return True
    else:
        return False


@login_required
@ratelimit(key='post:email', rate='5/m', block=True, method=['POST'])
def send_verification_email(request):
    email_sent = False
    if request.is_ajax:
        user = request.user
        user.account.generate_verification_code()
        user.account.save()
        user.save()
        EmailService.send_activate_email(user)
    return JsonResponse({
        'email_sent': email_sent
    })


@ratelimit(key='post:email', rate='5/m', block=True, method=['POST'])
def signup(request):
    if request.method != "POST":
        signup_form = SignupForm()
        return render(request, "accounts/signup.html", locals())

    signup_form = SignupForm(request.POST)

    if signup_form.is_valid():
        email = signup_form.cleaned_data['email']
        username = signup_form.cleaned_data['username']
        password = signup_form.cleaned_data['password']
        if User.objects.filter(email__iexact=email).exists():
            errors = signup_form._errors.setdefault("email", ErrorList())
            errors.append(u"Already Exists")
            return render(request, "accounts/signup.html", locals())
        
        user = signup_form.save(commit=False)
        user.set_password(password)
        user.save()
        user.account.generate_verification_code()
        user.account.save()


        try:
            EmailService.send_activate_email(user)
            email_sent = True
        except:
            email_sent_fail = False

        user = authenticate(username=username, password=password)
        django_login(request, user)
        next = request.GET.get('next', '')
        if next == '':
            next = '/accounts/profile/'
        return HttpResponseRedirect(next) 

    return render(request, "accounts/signup.html", locals())


@ratelimit(key='post:username', rate='5/m', block=True, method=['POST'])
def signin(request):
    if request.method != "POST":
        signin_form = SigninForm()
        return render(request, "accounts/login.html", locals())

    signin_form = SigninForm(request.POST)

    if not signin_form.is_valid():
        login_err = True
        return render(request, "accounts/login.html",locals())
        
    username = signin_form.cleaned_data['username']
    password = signin_form.cleaned_data['password']
    key = 'email__iexact' if '@' in username else 'username__iexact'
    if User.objects.filter(**{key: username}).exists():
        user = User.objects.get(**{key: username})
        user = authenticate(username=username, password=password)
        if user is None:
            login_err = True
            return render(request, "accounts/login.html", locals())
    else:
        login_err = True
        return render(request, "accounts/login.html", locals())

    django_login(request, user)
    next = request.GET.get('next', '')
    if next == '':
        next = '/accounts/profile/'
    return HttpResponseRedirect(next)

@ratelimit(key='post:email', rate='10/m', block=True, method=['POST'])
def reset_password(request):
    if 'email' not in request.GET and 'email' not in request.POST:
        reset_missing_email = True
        return render(request, "accounts/reset_password.html", locals())

    if 'email' in request.POST and 'verification_code' not in request.GET:
        email_sent = True
        email = request.POST.get('email')
        try:
            user = User.objects.get(email__iexact=email)
            user.account.generate_verification_code()
            user.account.save()
            EmailService.send_verification_email(user)
        except:
            email_not_exist = True
        return render(request, "accounts/reset_password.html", locals())

    if 'verification_code' not in request.GET:
        verification_code_error = True
        return render(request, "accounts/reset_password.html", locals())

    email = request.GET.get('email')
    verification_code = request.GET.get('verification_code')

    forgot_form = ResetPasswordForm(request.POST)

    if _is_bad_verification(email, verification_code):
        bad_verification = True
        return render(request, "accounts/reset_password.html", locals())

    if request.method != 'POST':
        return render(request, "accounts/reset_password.html", locals())

    if not forgot_form.is_valid():
        return render(request, "accounts/reset_password.html", locals())

    if forgot_form.cleaned_data['password'] != forgot_form.cleaned_data['confirm_password']:
        confirm_password_error = True
        return render(request, "accounts/reset_password.html", locals())

    user = User.objects.get(email=forgot_form.cleaned_data['email'])
    user.set_password(forgot_form.cleaned_data['password'])
    user.save()

    account = user.account
    account.verification_code = "%s" % timezone.now()
    account.expire_at = timezone.now()
    account.save()

    user = authenticate(username=user.username, password=forgot_form.cleaned_data['password'])
    django_login(request, user)

    next = request.GET.get('next', '')
    if next == '':
        next = '/accounts/profile/'
    return HttpResponseRedirect(next)

@login_required
def profile(request):
    account = Accounts.objects.get(user=request.user)

    if request.method != "POST":
        return render(request, "accounts/dashboard.html", locals())

    if 'edit' in request.POST:
        #user has clicked on edit
        editInfo = True
    elif 'submit' in request.POST:
        #user has clicked on submit
        editInfo = False
        editProfile_form = EditUserProfileForm(request.POST)

        if editProfile_form.is_valid():
            account.first_name = editProfile_form.cleaned_data['first_name']
            account.last_name = editProfile_form.cleaned_data['last_name']
            account.save()
            update_success = True
            return render(request, "accounts/dashboard.html",locals())

    return render(request, "accounts/dashboard.html", locals())    


@login_required
def sites(request):

    account = Accounts.objects.get(user=request.user)
    websites = Website.objects.filter(user=request.user)

    if request.method == "POST":

        #create delete form
        deleteForm = DeleteSiteForm(request.POST)
        print "post"
        #create Site
        if 'createSite' in request.POST:
            return HttpResponseRedirect("/sites/selectTemplate")
        elif 'deleteBtn' in request.POST:
            if deleteForm.is_valid():
                domain = deleteForm.cleaned_data['domain']

                if not Website.objects.filter(domain=domain).exists():
                    # website does not exist, do nothing
                    print "error. Website does not exist, this should not "
                    return render(request, "accounts/dashboard.html", locals())
                else:
                    #delete the website
                    website = Website.objects.get(domain = domain)
                    website.template.delete()
                    website.delete()
        elif 'viewBtn' in request.POST:
            if 'domain' in request.POST:
                domain = request.POST.get('domain')
                return HttpResponseRedirect("/website/" + domain)
        elif 'editBtn' in request.POST:
            if 'domain' in request.POST:
                domain = request.POST.get('domain')
                return HttpResponseRedirect("/sites/editPage?domain=" + domain)
        elif 'downloadBtn' in request.POST:
            print "Downloading page"
            if 'domain' in request.POST:
                domain = request.POST.get('domain')
                return HttpResponseRedirect( "/sites/download_site/" + domain)
                #return HttpResponseRedirect( "/sites/")

    #otherwise render site page
    return render(request, "accounts/sites.html", locals())


def signout(request):
    django_logout(request)
    return render(request, "accounts/signout.html", locals())


@login_required
def view_files(request):
    files = File.objects.filter(user=request.user)
    return render(request, "accounts/file.html", locals())


@ratelimit(key='post:file', rate='10/m', block=True, method=['POST'])
@login_required
def upload_file(request):
    if request.is_ajax():
        f = request.FILES['file']
        file = File.objects.create(content=f, user=request.user)
        ########################################################
        #
        # WARNING: WEAK FILE TYPE VALIDATION HERE. MAY CAUSE SECURITY PROBLEM. 
        # I WOULD FIX IT LATER, BUT JUST LEAVE IT FOR NOW FOR TESTING VERSION
        #
        ########################################################
        valid_preview_extentions = ['jpg', 'jpeg', 'png', 'gif']
        if file.content.name.split('.')[-1].lower() in valid_preview_extentions:
            file.preview = True
        file.title = ''.join(file.content.name.split('.')[:-1])
        file.save()
        return JsonResponse({
            'success': True
            })
    return render(request, "accounts/upload_file.html")


@login_required
def delete_file(request, file_id):
    file = File.objects.filter(id=file_id)
    if len(file) == 0:
        return JsonResponse({'success': False, "code": ErrorCode.NO_SUCH_FILE})
    file = file[0]
    if request.user.id != file.user.id:
        return JsonResponse({'success': False, "code": ErrorCode.NO_PERMISSION})

    try:
        name = file.content.name
        file.delete()
        os.remove(os.path.join(settings.MEDIA_ROOT, name))
    except:
        return JsonResponse({'success': False, "code": ErrorCode.NO_PERMISSION})

    return JsonResponse({'success': True, "file_id": file_id})

########################################################
#
# TODO: PROVIDE EDIT FUNCTION FOR USER TO CHANGE THEIR FILE NICK TITLE
# AS WELL AS FILE STATUS TO PUBLIC OR PRIVATE. WOULD DO LATER.
#
########################################################
@login_required
def edit_file(request, file_id):
    file = File.objects.filter(id=file_id)
    if len(file) == 0:
        return JsonResponse({'success': False, "code": ErrorCode.NO_SUCH_FILE})
    file = file[0]
    if request.user.id != file.user.id:
        return JsonResponse({'success': False, "code": ErrorCode.NO_PERMISSION})
    file.status = int(request.POST['action'])
    file.save()
    return JsonResponse({'success': True, "file_id": file_id, 'file_status': file.status})
