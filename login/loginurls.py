
from django.urls import path

from .loginurls import LoginView



urlpatterns = [
    path('login/', LoginView.as_view(), name='login'),

]